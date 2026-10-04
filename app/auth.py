import hashlib
import re
import secrets
from datetime import timedelta

import phonenumbers
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.db import connection, link_request_is_expired, timestamp, utc_now

bearer = HTTPBearer(auto_error=False)


def normalize_phone(phone: str) -> str:
    try:
        parsed = phonenumbers.parse(phone, None)
    except phonenumbers.NumberParseException as exc:
        raise ValueError("Enter a phone number with its country code, for example +14155552671.") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError("Enter a valid phone number with its country code.")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_link_request(username: str, phone: str) -> tuple[str, str]:
    clean_name = re.sub(r"\s+", " ", username).strip()
    if not 2 <= len(clean_name) <= 50:
        raise ValueError("Username must be between 2 and 50 characters.")
    normalized_phone = normalize_phone(phone)
    code = secrets.token_urlsafe(24)
    token = secrets.token_urlsafe(32)
    with connection() as conn:
        conn.execute(
            """INSERT INTO link_requests
               (code, username, phone, session_token_hash, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (code, clean_name, normalized_phone, hash_token(token), timestamp()),
        )
    return code, token


def create_browser_account(username: str, phone: str) -> tuple[str, dict]:
    clean_name = re.sub(r"\s+", " ", username).strip()
    if not 2 <= len(clean_name) <= 50:
        raise ValueError("Username must be between 2 and 50 characters.")
    normalized_phone = normalize_phone(phone)
    token = secrets.token_urlsafe(32)
    token_hash = hash_token(token)
    expires_at = timestamp(utc_now() + timedelta(days=settings.session_ttl_days))
    with connection() as conn:
        existing = conn.execute(
            "SELECT id, username, phone, telegram_id FROM users WHERE phone = ?",
            (normalized_phone,),
        ).fetchone()
        if existing is None:
            cursor = conn.execute(
                """INSERT INTO users (username, phone, created_at)
                   VALUES (?, ?, ?)""",
                (clean_name, normalized_phone, timestamp()),
            )
            user_id = cursor.lastrowid
            user = {
                "id": user_id,
                "username": clean_name,
                "phone": normalized_phone,
                "telegram_id": None,
            }
        else:
            user_id = existing["id"]
            user = dict(existing)
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash, user_id, expires_at),
        )
    user["session_token_hash"] = token_hash
    return token, user


def create_summary_link_request(user_id: int, session_token_hash: str, summary: str) -> str:
    code = secrets.token_urlsafe(24)
    with connection() as conn:
        user = conn.execute(
            "SELECT username, phone FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if user is None:
            raise ValueError("Your account could not be found. Please sign in again.")
        conn.execute(
            """INSERT INTO link_requests
               (code, username, phone, session_token_hash, created_at, user_id, pending_summary)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                code,
                user["username"],
                user["phone"],
                session_token_hash,
                timestamp(),
                user_id,
                summary,
            ),
        )
    return code


def get_link_status(code: str) -> bool:
    with connection() as conn:
        row = conn.execute(
            "SELECT created_at, verified_at FROM link_requests WHERE code = ?", (code,)
        ).fetchone()
    if row is None or link_request_is_expired(row["created_at"]):
        raise HTTPException(status_code=404, detail="This Telegram link has expired. Please start again.")
    return row["verified_at"] is not None


def authenticate(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    with connection() as conn:
        row = conn.execute(
            """SELECT users.id, users.username, users.phone, users.telegram_id
                      , sessions.token_hash AS session_token_hash
               FROM sessions JOIN users ON users.id = sessions.user_id
               WHERE sessions.token_hash = ? AND sessions.expires_at > ?""",
            (hash_token(credentials.credentials), timestamp()),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.")
    return dict(row)


def complete_link(code: str, telegram_id: int) -> tuple[str, str | None]:
    with connection() as conn:
        request = conn.execute(
            "SELECT * FROM link_requests WHERE code = ?", (code,)
        ).fetchone()
        if request is None or request["verified_at"] is not None:
            raise ValueError("That link is invalid, expired, or already used. Start again on the website.")
        if link_request_is_expired(request["created_at"]):
            raise ValueError("That link has expired. Start again on the website.")

        if request["user_id"] is not None:
            existing = conn.execute(
                "SELECT id, telegram_id FROM users WHERE id = ?", (request["user_id"],)
            ).fetchone()
        else:
            existing = conn.execute(
                "SELECT id, telegram_id FROM users WHERE phone = ?", (request["phone"],)
            ).fetchone()
        if existing and existing["telegram_id"] not in (None, telegram_id):
            raise ValueError("This ObjectLens account is already linked to a different Telegram account.")
        other_user = conn.execute(
            "SELECT id FROM users WHERE telegram_id = ? AND id != ?",
            (telegram_id, existing["id"] if existing else -1),
        ).fetchone()
        if other_user:
            raise ValueError("This Telegram account is already linked to a different ObjectLens account.")
        if existing:
            user_id = existing["id"]
            conn.execute(
                "UPDATE users SET username = ?, telegram_id = ? WHERE id = ?",
                (request["username"], telegram_id, user_id),
            )
        else:
            cursor = conn.execute(
                """INSERT INTO users (username, phone, telegram_id, created_at)
                   VALUES (?, ?, ?, ?)""",
                (request["username"], request["phone"], telegram_id, timestamp()),
            )
            user_id = cursor.lastrowid

        if request["user_id"] is None:
            expires_at = timestamp(utc_now() + timedelta(days=settings.session_ttl_days))
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (request["session_token_hash"], user_id, expires_at),
            )
        conn.execute(
            "UPDATE link_requests SET verified_at = ? WHERE code = ?",
            (timestamp(), code),
        )
        pending_summary = request["pending_summary"]
    return request["username"], pending_summary
