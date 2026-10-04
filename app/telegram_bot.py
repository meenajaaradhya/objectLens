import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from app.auth import complete_link
from app.config import settings
from app.db import connection, link_request_is_expired
from app.logging_utils import configure_secret_redaction

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
configure_secret_redaction(settings.bot_token)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    code = context.args[0] if context.args else ""
    with connection() as conn:
        request = conn.execute(
            "SELECT created_at FROM link_requests WHERE code = ?", (code,)
        ).fetchone()
    if not request or link_request_is_expired(request["created_at"]):
        await update.message.reply_text(
            "Welcome to ObjectLens! Start account linking from the ObjectLens website to get a fresh link."
        )
        return
    if not update.effective_user:
        await update.message.reply_text("I couldn't verify your Telegram account. Please try opening the link again.")
        return
    try:
        username, pending_summary = complete_link(code, update.effective_user.id)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return
    if pending_summary:
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text=pending_summary,
        )
    await update.message.reply_text(
        (
            f"You're linked, {username}! Your ObjectLens conversation summary is above."
            if pending_summary
            else f"You're linked, {username}! Return to ObjectLens in your browser."
        ),
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text("Linking cancelled.")


def main() -> None:
    if not settings.bot_token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in .env before starting the Telegram bot.")
    application = Application.builder().token(settings.bot_token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    logger.info("Starting ObjectLens Telegram polling bot.")
    application.run_polling()


if __name__ == "__main__":
    main()
