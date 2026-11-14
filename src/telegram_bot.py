"""Telegram control surface: /run /status /help."""

from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from . import config
from .pipeline import format_summary, run_relay

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "YouTube Relay Bot\n\n"
    "/run     — trigger the relay now\n"
    "/status  — last run summary\n"
    "/next    — next scheduled runs\n"
    "/help    — this message"
)

# One relay at a time: two concurrent runs would double-upload the same video.
_relay_lock = asyncio.Lock()


def _is_allowed(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.id == config.TELEGRAM_ALLOWED_CHAT_ID)


async def _execute_relay(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """Run the blocking pipeline in a worker thread so polling keeps responding."""
    async with _relay_lock:
        summary = await asyncio.to_thread(run_relay)
    context.application.bot_data["last_summary"] = summary
    return summary


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    await update.message.reply_text(HELP_TEXT)


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    if _relay_lock.locked():
        await update.message.reply_text("A relay is already running — wait for it to finish.")
        return

    await update.message.reply_text("Starting relay pipeline...")
    summary = await _execute_relay(context)
    await update.message.reply_text(format_summary(summary))


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    if _relay_lock.locked():
        await update.message.reply_text("Relay currently running...")
        return
    last = context.application.bot_data.get("last_summary")
    if not last:
        await update.message.reply_text("No run recorded yet.")
        return
    await update.message.reply_text(format_summary(last, "Last run"))


async def cmd_next(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    jobs = context.application.job_queue.jobs() if context.application.job_queue else ()
    if not jobs:
        await update.message.reply_text("No scheduled jobs registered.")
        return
    lines = [f"  {job.name}: {job.next_t}" for job in jobs]
    await update.message.reply_text("Scheduled runs:\n" + "\n".join(lines))


async def scheduled_relay(context: ContextTypes.DEFAULT_TYPE) -> None:
    """job_queue callback for the morning and evening runs."""
    logger.info("Scheduled relay triggered")
    if _relay_lock.locked():
        logger.warning("Previous relay still running — skipping this scheduled run")
        return

    summary = await _execute_relay(context)
    await context.bot.send_message(
        chat_id=config.TELEGRAM_ALLOWED_CHAT_ID,
        text=format_summary(summary, "Scheduled relay done"),
    )


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error in handler", exc_info=context.error)


def build_app() -> Application:
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("next", cmd_next))
    app.add_error_handler(_on_error)
    return app
