import asyncio
import logging
import os
import signal
import time

from cryptography.fernet import Fernet, InvalidToken as DecryptionInvalidToken
from sqlalchemy import create_engine, text
from telegram import Update
from telegram.error import InvalidToken as TelegramInvalidToken
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bot-center-worker")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
ENCRYPTION_KEY = os.getenv("BOT_TOKEN_ENCRYPTION_KEY", "").strip()
POLL_SECONDS = max(10, int(os.getenv("BOT_WORKER_POLL_SECONDS", "30")))

if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required")
if not ENCRYPTION_KEY:
    raise RuntimeError("BOT_TOKEN_ENCRYPTION_KEY is required")

cipher = Fernet(ENCRYPTION_KEY.encode())
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
running = {}
stopping = asyncio.Event()


def update_runtime(bot_id: int, status: str, seen_at: int | None = None):
    """Persist worker health without ever reading or exposing the bot token."""
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE managed_bots SET runtime_status = :status, last_seen_at = :seen WHERE id = :bot_id"),
            {"status": status, "seen": seen_at, "bot_id": bot_id},
        )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = context.application.bot.username or "بوتك"
    await update.effective_message.reply_text(
        f"أهلاً بك في @{name} 🤖\nالبوت يعمل الآن عبر Bot Center."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "الأوامر المتاحة:\n/start - بدء الاستخدام\n/help - المساعدة\n/ping - فحص حالة البوت"
    )


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Pong! ✅ البوت يعمل.")


async def stop_application(app: Application):
    try:
        if app.updater and app.updater.running:
            await app.updater.stop()
        if app.running:
            await app.stop()
        await app.shutdown()
    except Exception:
        log.exception("Failed to stop a bot cleanly")


async def launch_bot(bot_id: int, username: str, encrypted_token: str):
    try:
        token = cipher.decrypt(encrypted_token.encode()).decode()
        app = Application.builder().token(token).build()
        app.add_handler(CommandHandler("start", start_command))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("ping", ping_command))

        await app.initialize()
        await app.start()
        if not app.updater:
            raise RuntimeError("Polling updater is unavailable")
        await app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=False,
        )
        running[bot_id] = {"app": app, "username": username}
        await asyncio.to_thread(update_runtime, bot_id, "running", int(time.time()))
        log.info("Started @%s (record %s)", username, bot_id)
    except DecryptionInvalidToken:
        log.error("Could not decrypt token for bot record %s", bot_id)
        await asyncio.to_thread(update_runtime, bot_id, "error", None)
    except TelegramInvalidToken:
        log.error("Telegram rejected the saved token for bot record %s", bot_id)
        await asyncio.to_thread(update_runtime, bot_id, "error", None)
    except Exception as exc:
        log.error("Could not start @%s (record %s): %s", username, bot_id, type(exc).__name__)
        try:
            await asyncio.to_thread(update_runtime, bot_id, "error", None)
        except Exception:
            log.exception("Could not persist failed runtime status")
        try:
            if "app" in locals():
                await stop_application(app)
        except Exception:
            pass


def load_bot_records():
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT id, bot_username, token_encrypted FROM managed_bots ORDER BY id"
        )).mappings().all()
    return {int(row["id"]): (str(row["bot_username"]), str(row["token_encrypted"])) for row in rows}


async def sync_bots():
    try:
        records = await asyncio.to_thread(load_bot_records)
    except Exception:
        log.exception("Could not read managed bots from database")
        return

    # Stop bots removed from the platform.
    for bot_id in list(running):
        if bot_id not in records:
            item = running.pop(bot_id)
            await stop_application(item["app"])
            log.info("Stopped removed bot record %s", bot_id)

    # Refresh heartbeat for active bots and start newly added records.
    for bot_id, (username, encrypted_token) in records.items():
        if bot_id in running:
            try:
                await asyncio.to_thread(update_runtime, bot_id, "running", int(time.time()))
            except Exception:
                log.exception("Could not update heartbeat for bot record %s", bot_id)
        else:
            await launch_bot(bot_id, username, encrypted_token)


async def main():
    log.info("Bot Center worker starting; refresh interval=%ss", POLL_SECONDS)
    await sync_bots()
    while not stopping.is_set():
        try:
            await asyncio.wait_for(stopping.wait(), timeout=POLL_SECONDS)
        except asyncio.TimeoutError:
            await sync_bots()

    for bot_id, item in list(running.items()):
        await stop_application(item["app"])
        try:
            await asyncio.to_thread(update_runtime, bot_id, "stopped", None)
        except Exception:
            log.exception("Could not persist stopped status for bot record %s", bot_id)
        running.pop(bot_id, None)
    engine.dispose()
    log.info("Bot Center worker stopped")


def request_stop(*_):
    stopping.set()


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:
            pass
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()
