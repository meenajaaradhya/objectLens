import os
from dataclasses import dataclass
from pathlib import Path
import tomllib

from dotenv import load_dotenv

load_dotenv()

SECRETS_FILE = Path(__file__).resolve().parent.parent / "secrets.toml"


def _read_secrets() -> dict:
    if not SECRETS_FILE.exists():
        return {}
    with SECRETS_FILE.open("rb") as secrets_file:
        return tomllib.load(secrets_file)


def _setting(env_name: str, section: str, key: str, default):
    env_value = os.getenv(env_name)
    if env_value is not None and env_value.strip():
        return env_value
    return _SECRETS.get(section, {}).get(key, default)


@dataclass(frozen=True)
class Settings:
    database_path: str
    bot_token: str
    bot_username: str
    ai_provider_order: tuple[str, ...]
    gemini_api_key: str
    gemini_models: tuple[str, ...]
    groq_api_key: str
    groq_model: str
    max_upload_bytes: int
    link_code_ttl_minutes: int
    session_ttl_days: int


_SECRETS = _read_secrets()


def get_settings() -> Settings:
    configured_order = _setting(
        "AI_PROVIDER_ORDER", "ai", "provider_order", ["gemini", "groq"]
    )
    if isinstance(configured_order, str):
        configured_order = configured_order.split(",")
    provider_order = tuple(
        name.strip().lower()
        for name in configured_order
        if name.strip()
    )
    supported = {"gemini", "groq"}
    invalid = [name for name in provider_order if name not in supported]
    if invalid:
        raise ValueError(f"Unsupported AI providers in AI_PROVIDER_ORDER: {', '.join(invalid)}")

    configured_gemini_models = _setting("GEMINI_MODELS", "ai", "gemini_models", None)
    if configured_gemini_models is None:
        configured_gemini_models = _setting(
            "GEMINI_MODEL",
            "ai",
            "gemini_model",
            ["gemini-3.7-flash", "gemini-3.8-flash"],
        )
    if isinstance(configured_gemini_models, str):
        configured_gemini_models = configured_gemini_models.split(",")
    gemini_models = tuple(
        dict.fromkeys(model.strip() for model in configured_gemini_models if model.strip())
    )
    if not gemini_models:
        raise ValueError("Configure at least one Gemini model in GEMINI_MODELS or secrets.toml.")

    return Settings(
        database_path=_setting("DATABASE_PATH", "app", "database_path", "objectlens.sqlite3"),
        bot_token=_setting("TELEGRAM_BOT_TOKEN", "telegram", "bot_token", ""),
        bot_username=_setting("TELEGRAM_BOT_USERNAME", "telegram", "bot_username", "").lstrip("@"),
        ai_provider_order=provider_order,
        gemini_api_key=_setting("GEMINI_API_KEY", "ai", "gemini_api_key", ""),
        gemini_models=gemini_models,
        groq_api_key=_setting("GROQ_API_KEY", "ai", "groq_api_key", ""),
        groq_model=_setting(
            "GROQ_MODEL", "ai", "groq_model", "qwen/qwen3.8-27b"
        ),
        max_upload_bytes=int(
            _setting("MAX_UPLOAD_BYTES", "app", "max_upload_bytes", 10 * 1024 * 1024)
        ),
        link_code_ttl_minutes=int(
            _setting("LINK_CODE_TTL_MINUTES", "app", "link_code_ttl_minutes", 10)
        ),
        session_ttl_days=int(_setting("SESSION_TTL_DAYS", "app", "session_ttl_days", 30)),
    )


settings = get_settings()
