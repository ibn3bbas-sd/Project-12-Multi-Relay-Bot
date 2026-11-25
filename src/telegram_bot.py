"""Telegram control surface: /run /fill /fbfill /igfill /fbsfill /xfill /ttfill /status /stats /next /help."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from telegram import LinkPreviewOptions, Update
from telegram.ext import Application, CommandHandler, ContextTypes, Defaults

from . import config, health, stats_db
from .pipeline import (
    FB_FILL_DEFAULT,
    FB_FILL_MAX,
    SHORTS_FILL_DEFAULT,
    SHORTS_FILL_MAX,
    format_fb_fill,
    format_shorts_fill,
    format_summary,
    is_quiet,
    run_fb_fill,
    run_relay,
    run_shorts_fill,
)

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "🤖 Multi-Relay Bot\n"
    "▶️ YouTube"
    + (" · 📘 Facebook" if config.FB_ENABLED else "")
    + (" · 📸 Instagram" if config.IG_ENABLED else "")
    + (" · 📖 FB Stories" if config.FBS_ENABLED else "")
    + (" · ✖️ X" if config.X_ENABLED else "")
    + (" · 🎵 TikTok" if config.TIKTOK_ENABLED else "")
    + "\n━━━━━━━━━━━━━━\n"
    "🚀 /run — relay new videos now\n"
    f"📦 /fill — relay new videos, then top today up to {config.DAILY_UPLOAD_TARGET}\n"
    + (f"📘 /fbfill [n] — older videos to Facebook (default {FB_FILL_DEFAULT}, max {FB_FILL_MAX})\n" if config.FB_ENABLED else "")
    + (f"📸 /igfill [n] — Shorts to Instagram now (default {SHORTS_FILL_DEFAULT}, max {SHORTS_FILL_MAX})\n" if config.IG_ENABLED else "")
    + (f"📖 /fbsfill [n] — Shorts to Facebook Stories now (default {SHORTS_FILL_DEFAULT}, max {SHORTS_FILL_MAX})\n" if config.FBS_ENABLED else "")
    + (f"✖️ /xfill [n] — Shorts to X now (default {SHORTS_FILL_DEFAULT}, max {SHORTS_FILL_MAX})\n" if config.X_ENABLED else "")
    + (f"🎵 /ttfill [n] — Shorts to TikTok now (default {SHORTS_FILL_DEFAULT}, max {SHORTS_FILL_MAX})\n" if config.TIKTOK_ENABLED else "")
    + "📊 /status — last run summary\n"
    "📈 /stats [days] — posts per platform and per day (default 7, max 30)\n"
    "⏰ /next — next scheduled runs\n"
    "❓ /help — this message"
)

# One relay at a time: two concurrent runs would double-upload the same video.
_relay_lock = asyncio.Lock()
_running_since: datetime | None = None  # start of the relay holding _relay_lock (watchdog)
_started_at = datetime.now(timezone.utc)


def _is_allowed(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.id == config.TELEGRAM_ALLOWED_CHAT_ID)


@asynccontextmanager
async def _relay_slot():
    """Hold the relay lock and record since when (for the stuck-relay watchdog)."""
    global _running_since
    async with _relay_lock:
        _running_since = datetime.now(timezone.utc)
        try:
            yield
        finally:
            _running_since = None


async def _execute_relay(context: ContextTypes.DEFAULT_TYPE, backfill: bool = False) -> dict:
    """Run the blocking pipeline in a worker thread so polling keeps responding."""
    async with _relay_slot():
        summary = await asyncio.to_thread(run_relay, backfill)
    context.application.bot_data["last_summary"] = summary
    return summary


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    await update.message.reply_text(HELP_TEXT)


async def _manual_run(update: Update, context: ContextTypes.DEFAULT_TYPE, backfill: bool) -> None:
    if not _is_allowed(update):
        return
    if _relay_lock.locked():
        await update.message.reply_text("⏳ A relay is already running — wait for it to finish.")
        return

    await update.message.reply_text("🚀 Starting relay...")
    summary = await _execute_relay(context, backfill)
    await update.message.reply_text(format_summary(summary))


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _manual_run(update, context, backfill=False)


async def cmd_fill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _manual_run(update, context, backfill=True)


async def cmd_fbfill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    if not config.FB_ENABLED:
        await update.message.reply_text("🚫 Facebook is disabled (FB_ENABLED=false).")
        return
    if _relay_lock.locked():
        await update.message.reply_text("⏳ A relay is already running — wait for it to finish.")
        return
    try:
        limit = int(context.args[0]) if context.args else FB_FILL_DEFAULT
    except ValueError:
        await update.message.reply_text(f"Usage: /fbfill [n]  (1–{FB_FILL_MAX})")
        return
    limit = max(1, min(limit, FB_FILL_MAX))

    await update.message.reply_text(f"📘 Posting up to {limit} older video(s) to Facebook...")
    async with _relay_slot():
        result = await asyncio.to_thread(run_fb_fill, limit)
    await update.message.reply_text(format_fb_fill(result))


async def _shorts_fill(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       key: str, name: str, enabled: bool, flag: str, command: str) -> None:
    if not _is_allowed(update):
        return
    if not enabled:
        await update.message.reply_text(f"🚫 {name} is disabled ({flag}=false).")
        return
    if _relay_lock.locked():
        await update.message.reply_text("⏳ A relay is already running — wait for it to finish.")
        return
    try:
        limit = int(context.args[0]) if context.args else SHORTS_FILL_DEFAULT
    except ValueError:
        await update.message.reply_text(f"Usage: /{command} [n]  (1–{SHORTS_FILL_MAX})")
        return
    limit = max(1, min(limit, SHORTS_FILL_MAX))

    await update.message.reply_text(f"📤 Posting up to {limit} Short(s) to {name}...")
    async with _relay_slot():
        result = await asyncio.to_thread(run_shorts_fill, key, limit)
    await update.message.reply_text(format_shorts_fill(result))


async def cmd_igfill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _shorts_fill(update, context, "ig", "Instagram", config.IG_ENABLED, "IG_ENABLED", "igfill")


async def cmd_fbsfill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _shorts_fill(update, context, "fbs", "Facebook Stories", config.FBS_ENABLED, "FBS_ENABLED", "fbsfill")


async def cmd_xfill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _shorts_fill(update, context, "x", "X", config.X_ENABLED, "X_ENABLED", "xfill")


async def cmd_ttfill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _shorts_fill(update, context, "tt", "TikTok", config.TIKTOK_ENABLED, "TIKTOK_ENABLED", "ttfill")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    if _relay_lock.locked():
        await update.message.reply_text("⏳ Relay currently running...")
        return
    last = context.application.bot_data.get("last_summary")
    if not last:
        await update.message.reply_text("📭 No run recorded yet.")
        return
    await update.message.reply_text(format_summary(last, "Last scheduled run"))


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    try:
        days = int(context.args[0]) if context.args else 7
    except ValueError:
        await update.message.reply_text("Usage: /stats [days]  (1–30)")
        return
    try:
        text = await asyncio.to_thread(stats_db.build_report, days)
    except Exception as exc:  # noqa: BLE001
        logger.exception("stats report failed")
        text = f"⚠️ Stats unavailable: {type(exc).__name__}: {exc}"
    await update.message.reply_text(text)


async def cmd_next(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    jobs = context.application.job_queue.jobs() if context.application.job_queue else ()
    if not jobs:
        await update.message.reply_text("📭 No scheduled jobs registered.")
        return
    lines = [f"• {job.name}: {job.next_t:%Y-%m-%d %H:%M %Z}" if job.next_t else f"• {job.name}: —" for job in jobs]
    await update.message.reply_text("⏰ Scheduled runs\n" + "\n".join(lines))


async def _scheduled(context: ContextTypes.DEFAULT_TYPE, backfill: bool, header: str) -> None:
    logger.info("Scheduled run triggered (%s)", context.job.name if context.job else "?")
    if _relay_lock.locked():
        logger.warning("Previous relay still running — skipping this scheduled run")
        return

    summary = await _execute_relay(context, backfill)
    if not backfill and is_quiet(summary):
        return  # frequent checks that find nothing stay silent
    await context.bot.send_message(
        chat_id=config.TELEGRAM_ALLOWED_CHAT_ID,
        text=format_summary(summary, header),
    )


async def scheduled_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Repeating job: relay anything new from the source channel."""
    await _scheduled(context, backfill=False, header="Scheduled check")


async def scheduled_backfill(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Daily job: relay new videos, then top the day up with older ones."""
    await _scheduled(context, backfill=True, header="Daily top-up done")


async def expiry_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Daily job: warn about credentials that expire within REMIND_DAYS."""
    text = await asyncio.to_thread(health.due_reminders)
    if text:
        await context.bot.send_message(chat_id=config.TELEGRAM_ALLOWED_CHAT_ID, text=text)


WATCH_LABELS = {"silent": "🔇 Bot silent", "stuck": "🐢 Relay stuck", "failing": "🛑 Runs failing"}


async def watchdog(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Repeating job: alert once per problem, then once when it clears."""
    problems = await asyncio.to_thread(health.check, _started_at, _running_since)
    active: dict = context.application.bot_data.setdefault("watchdog_active", {})
    for kind, problem in problems.items():
        if problem and kind not in active:
            active[kind] = problem
            logger.warning("watchdog %s: %s", kind, problem)
            await context.bot.send_message(chat_id=config.TELEGRAM_ALLOWED_CHAT_ID,
                                           text=f"{WATCH_LABELS[kind]}\n━━━━━━━━━━━━━━\n{problem}")
        elif not problem and kind in active:
            del active[kind]
            await context.bot.send_message(chat_id=config.TELEGRAM_ALLOWED_CHAT_ID,
                                           text=f"✅ Recovered: {WATCH_LABELS[kind].split(' ', 1)[1].lower()} cleared")


async def send_offline_reports(context: ContextTypes.DEFAULT_TYPE) -> None:
    """One-off job: deliver the reports of runs made while Telegram was down."""
    reports = context.application.bot_data.pop("offline_reports", [])
    text = f"📡 Telegram was unreachable; the bot kept relaying. {len(reports)} report(s):"
    await context.bot.send_message(chat_id=config.TELEGRAM_ALLOWED_CHAT_ID, text=text)
    for report in reports[-10:]:
        await context.bot.send_message(chat_id=config.TELEGRAM_ALLOWED_CHAT_ID, text=report)


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error in handler", exc_info=context.error)


def build_app() -> Application:
    defaults = Defaults(link_preview_options=LinkPreviewOptions(is_disabled=True))
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).defaults(defaults).build()
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("fill", cmd_fill))
    app.add_handler(CommandHandler("fbfill", cmd_fbfill))
    app.add_handler(CommandHandler("igfill", cmd_igfill))
    app.add_handler(CommandHandler("fbsfill", cmd_fbsfill))
    app.add_handler(CommandHandler("xfill", cmd_xfill))
    app.add_handler(CommandHandler("ttfill", cmd_ttfill))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("next", cmd_next))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_error_handler(_on_error)
    return app
