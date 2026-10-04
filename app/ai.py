import asyncio
import base64
import json
import logging
from typing import Any

from app.config import settings
from prompts import SUMMARY_REQUEST_PROMPT, SYSTEM_PROMPT

logger = logging.getLogger(__name__)

IDENTIFY_PROMPT = (
    "Identify the main object in this image and explain it briefly for a curious beginner. "
    "Return ONLY a JSON object with exactly these keys: "
    '{"object_name":"short name","introduction":"2-4 plain-language sentences",'
    '"aspects":["3-6 short relevant deep-dive aspect labels"]}. '
    "Choose aspects appropriate to this subject: for example mechanical, electrical, biological, "
    "structural, materials, or how it is made. Do not guess details that are not visible."
)


class AIUnavailableError(RuntimeError):
    pass


def _available_providers() -> list[tuple[str, str, str]]:
    providers: list[tuple[str, str, str]] = []
    for name in settings.ai_provider_order:
        if name == "gemini" and settings.gemini_api_key:
            providers.extend(
                ("gemini", settings.gemini_api_key, model)
                for model in settings.gemini_models
            )
        elif name == "groq" and settings.groq_api_key:
            providers.append(("groq", settings.groq_api_key, settings.groq_model))
    return providers


async def _generate(
    provider: str,
    api_key: str,
    model: str,
    system: str,
    prompt: Any,
    image: bytes | None,
    image_mime_type: str | None,
):
    if provider == "gemini":
        return await asyncio.to_thread(
            _generate_gemini, api_key, model, system, prompt, image, image_mime_type
        )
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
    content: Any = prompt
    if image is not None:
        content = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": (
                        f"data:{image_mime_type};base64,"
                        f"{base64.b64encode(image).decode('ascii')}"
                    )
                },
            },
        ]
    request = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
    }
    if image is not None:
        request["response_format"] = {"type": "json_object"}
    response = client.chat.completions.create(**request)
    result = response.choices[0].message.content
    if not result:
        raise RuntimeError("Groq returned an empty response.")
    return result


def _generate_gemini(
    api_key: str,
    model: str,
    system: str,
    prompt: Any,
    image: bytes | None,
    image_mime_type: str | None,
) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    contents: list[Any] = [prompt]
    if image is not None:
        contents.insert(0, types.Part.from_bytes(data=image, mime_type=image_mime_type))
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json" if image is not None else "text/plain",
        ),
    )
    if not response.text:
        raise RuntimeError("Gemini returned an empty response.")
    return response.text


async def generate_text(system: str, prompt: str) -> str:
    return await _with_failover(system, prompt, None, None)


async def identify_image(image: bytes, image_mime_type: str) -> dict[str, Any]:
    raw = await _with_failover(SYSTEM_PROMPT, IDENTIFY_PROMPT, image, image_mime_type)
    try:
        result = json.loads(raw)
        name = str(result["object_name"]).strip()
        introduction = str(result["introduction"]).strip()
        aspects = [str(item).strip() for item in result["aspects"] if str(item).strip()]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError("The AI response did not include a valid object identification.") from exc
    if not name or not introduction or not aspects:
        raise RuntimeError("The AI response was missing the object name, introduction, or aspects.")
    return {"object_name": name, "introduction": introduction, "aspects": aspects[:6]}


async def create_summary(transcript: str) -> str:
    prompt = f"{SUMMARY_REQUEST_PROMPT}\n\nConversation:\n{transcript}"
    summary = (await generate_text(SYSTEM_PROMPT, prompt)).strip()
    if len(summary) > 3900:
        summary = summary[:3897].rsplit(" ", 1)[0] + "..."
    return summary


async def _with_failover(
    system: str, prompt: str, image: bytes | None, image_mime_type: str | None
) -> str:
    providers = _available_providers()
    if not providers:
        raise AIUnavailableError(
            "AI is not configured yet. Please check the provider settings."
        )

    for provider, api_key, model in providers:
        try:
            return await _generate(
                provider, api_key, model, system, prompt, image, image_mime_type
            )
        except Exception as exc:
            logger.warning(
                "AI provider %s model %s failed (%s); trying the next configured model.",
                provider,
                model,
                type(exc).__name__,
            )
    raise AIUnavailableError(
        "ObjectLens AI is temporarily unavailable. Please try again shortly. "
        "If the problem continues, check your provider model and account limits."
    )
