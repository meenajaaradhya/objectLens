import logging
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.ai import AIUnavailableError, create_summary, generate_text, identify_image
from app.auth import (
    authenticate,
    create_browser_account,
    create_summary_link_request,
    get_link_status,
)
from app.config import settings
from app.db import connection, init_db, timestamp
from app.logging_utils import configure_secret_redaction
from prompts import SYSTEM_PROMPT

configure_secret_redaction(settings.bot_token)
logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="ObjectLens", version="1.0.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


class RegisterRequest(BaseModel):
    username: str
    phone: str


class MessageRequest(BaseModel):
    message: str


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/")
def home():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "gemini_models": settings.gemini_models,
        "groq_configured": bool(settings.groq_api_key),
        "groq_model": settings.groq_model if settings.groq_api_key else None,
    }


@app.post("/api/auth/register")
def register(request: RegisterRequest):
    try:
        token, user = create_browser_account(request.username, request.phone)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"username": user["username"], "session_token": token}


@app.get("/api/auth/link-status/{code}")
def link_status(code: str):
    return {"linked": get_link_status(code)}


@app.get("/api/auth/me")
def current_user(user: dict = Depends(authenticate)):
    return {"username": user["username"]}


@app.post("/api/auth/logout")
def logout(user: dict = Depends(authenticate)):
    with connection() as conn:
        conn.execute(
            "DELETE FROM sessions WHERE token_hash = ?",
            (user["session_token_hash"],),
        )
    return {"logged_out": True}


def get_conversation(conversation_id: int, user_id: int, conn):
    row = conn.execute(
        "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
        (conversation_id, user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    if row["ended_at"] is not None:
        raise HTTPException(status_code=409, detail="This conversation has ended.")
    return row


@app.post("/api/conversations")
def start_conversation(user: dict = Depends(authenticate)):
    with connection() as conn:
        cursor = conn.execute(
            "INSERT INTO conversations (user_id, created_at) VALUES (?, ?)",
            (user["id"], timestamp()),
        )
    return {"conversation_id": cursor.lastrowid}


@app.post("/api/conversations/{conversation_id}/photo")
async def upload_photo(
    conversation_id: int,
    photo: UploadFile = File(...),
    user: dict = Depends(authenticate),
):
    if photo.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(status_code=415, detail="Upload a JPEG, PNG, or WebP image.")
    image = await photo.read(settings.max_upload_bytes + 1)
    if len(image) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="The image is too large.")
    if not image:
        raise HTTPException(status_code=400, detail="The uploaded image is empty.")
    with connection() as conn:
        conversation = get_conversation(conversation_id, user["id"], conn)
        if conversation["object_name"]:
            raise HTTPException(status_code=409, detail="This conversation already has an identified object.")
    try:
        identified = await identify_image(image, photo.content_type)
    except AIUnavailableError as exc:
        logger.error("Object identification failed for all configured providers: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.exception("Object identification failed.")
        raise HTTPException(status_code=502, detail="The AI could not identify this image. Please try another photo.") from exc

    with connection() as conn:
        conversation = get_conversation(conversation_id, user["id"], conn)
        if conversation["object_name"]:
            raise HTTPException(status_code=409, detail="This conversation already has an identified object.")
        conn.execute(
            "UPDATE conversations SET object_name = ? WHERE id = ?",
            (identified["object_name"], conversation_id),
        )
        conn.execute(
            """INSERT INTO messages (conversation_id, role, content, created_at)
               VALUES (?, 'user', ?, ?)""",
            (conversation_id, "[Uploaded a photo]", timestamp()),
        )
        conn.execute(
            """INSERT INTO messages (conversation_id, role, content, created_at)
               VALUES (?, 'assistant', ?, ?)""",
            (conversation_id, identified["introduction"], timestamp()),
        )
    return identified


@app.post("/api/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: int,
    request: MessageRequest,
    user: dict = Depends(authenticate),
):
    message = request.message.strip()
    if not message or len(message) > 2000:
        raise HTTPException(status_code=400, detail="Message must be between 1 and 2,000 characters.")
    with connection() as conn:
        conversation = get_conversation(conversation_id, user["id"], conn)
        if not conversation["object_name"]:
            raise HTTPException(status_code=409, detail="Upload a photo and identify its object first.")
        prior = conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ).fetchall()
    history = "\n".join(f"{row['role']}: {row['content']}" for row in prior)
    system = (
        f"{SYSTEM_PROMPT} The user has uploaded an image identified as "
        f"'{conversation['object_name']}'. Keep this conversation exclusively about that subject "
        "and how it works, is structured, or functions. If asked about something else, politely redirect."
    )
    prompt = f"Conversation so far:\n{history}\n\nUser: {message}"
    try:
        answer = await generate_text(system, prompt)
    except AIUnavailableError as exc:
        logger.error("Chat response failed for all configured providers: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    with connection() as conn:
        get_conversation(conversation_id, user["id"], conn)
        conn.executemany(
            """INSERT INTO messages (conversation_id, role, content, created_at)
               VALUES (?, ?, ?, ?)""",
            [
                (conversation_id, "user", message, timestamp()),
                (conversation_id, "assistant", answer, timestamp()),
            ],
        )
    return {"answer": answer}


@app.post("/api/conversations/{conversation_id}/end")
async def end_conversation(
    conversation_id: int,
    user: dict = Depends(authenticate),
):
    with connection() as conn:
        conversation = conn.execute(
            "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user["id"]),
        ).fetchone()
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        if not conversation["object_name"]:
            raise HTTPException(status_code=409, detail="Identify an object before ending the conversation.")
        summary = conversation["summary"]
        if conversation["ended_at"] is None:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
            transcript = (
                f"Identified object: {conversation['object_name']}\n"
                + "\n".join(f"{row['role']}: {row['content']}" for row in rows)
            )
        else:
            transcript = None

    if summary is None:
        try:
            summary = await create_summary(transcript)
        except AIUnavailableError as exc:
            logger.error("Conversation summary failed for all configured providers: %s", exc)
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not settings.bot_token:
        raise HTTPException(status_code=503, detail="Set the Telegram bot token in secrets.toml.")
    if user["telegram_id"] is not None:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{settings.bot_token}/sendMessage",
                    json={"chat_id": user["telegram_id"], "text": summary},
                )
                response.raise_for_status()
                result = response.json()
                if not result.get("ok"):
                    raise RuntimeError(str(result))
        except (httpx.HTTPError, RuntimeError) as exc:
            logger.exception("Telegram summary delivery failed.")
            raise HTTPException(
                status_code=502,
                detail="Could not send the summary to Telegram. Please try again.",
            ) from exc
        with connection() as conn:
            conn.execute(
                """UPDATE conversations SET ended_at = COALESCE(ended_at, ?), summary = ?
                   WHERE id = ? AND user_id = ?""",
                (timestamp(), summary, conversation_id, user["id"]),
            )
        return {"summary": summary, "sent": True}

    if not settings.bot_username:
        raise HTTPException(status_code=503, detail="Set the Telegram bot username in secrets.toml.")
    try:
        code = create_summary_link_request(user["id"], user["session_token_hash"], summary)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with connection() as conn:
        conn.execute(
            """UPDATE conversations SET ended_at = COALESCE(ended_at, ?), summary = ?
               WHERE id = ? AND user_id = ?""",
            (timestamp(), summary, conversation_id, user["id"]),
        )
    return {
        "summary": summary,
        "sent": False,
        "link_code": code,
        "telegram_link": f"https://t.me/{settings.bot_username}?start={code}",
    }
