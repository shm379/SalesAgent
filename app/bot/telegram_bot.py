"""Telegram bot entrypoint - wires python-telegram-bot handlers to the
compiled LangGraph graph. Run with: python -m app.bot.telegram_bot
(or via the project's main.py)."""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from app.bot import scheduler, threads_store
from app.config import get_settings
from app.graph.builder import build_graph

logger = logging.getLogger(__name__)


async def _run_turn(context: ContextTypes.DEFAULT_TYPE, chat_id: int, invoke_input: dict) -> str:
    graph = context.application.bot_data["graph"]
    config = {"configurable": {"thread_id": str(chat_id)}}
    try:
        result = await graph.ainvoke(invoke_input, config=config)
    except Exception:  # noqa: BLE001
        logger.exception("Graph invocation failed for chat_id=%s", chat_id)
        return "ببخشید، یه مشکل فنی پیش اومد. چند لحظه دیگه دوباره امتحان کن 🙏"

    if result.get("purchase_confirmed"):
        settings = get_settings()
        threads_store.set_purchase_confirmed(settings.bot_threads_sqlite_path, str(chat_id))
        scheduler.cancel_followups(context.application.job_queue, chat_id)

    return result.get("final_response") or "متاسفانه در حال حاضر نمی‌تونم پاسخ بدم، لطفاً دوباره امتحان کن."


async def _after_turn(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc).isoformat()
    threads_store.upsert_thread(settings.bot_threads_sqlite_path, str(chat_id), now)
    scheduler.schedule_followups(context.application.job_queue, chat_id)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    text = update.message.text or ""

    reply = await _run_turn(context, chat_id, {"messages": [HumanMessage(content=text)], "input_type": "text"})
    await update.message.reply_text(reply)
    await _after_turn(context, chat_id)


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    photo = update.message.photo[-1]  # highest resolution variant
    telegram_file = await photo.get_file()
    photo_bytes = await telegram_file.download_as_bytearray()
    image_base64 = base64.b64encode(bytes(photo_bytes)).decode()
    caption = update.message.caption or "این محصول رو دارید؟ یه چیز مشابه پیشنهاد بده."

    reply = await _run_turn(
        context,
        chat_id,
        {
            "messages": [HumanMessage(content=caption)],
            "input_type": "photo",
            "image_base64": image_base64,
        },
    )
    await update.message.reply_text(reply)
    await _after_turn(context, chat_id)


async def post_init(application: Application) -> None:
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    settings = get_settings()
    conn = await aiosqlite.connect(str(settings.sqlite_path))
    checkpointer = AsyncSqliteSaver(conn)

    application.bot_data["sqlite_conn"] = conn
    application.bot_data["graph"] = build_graph(checkpointer)

    await scheduler.reconcile_on_startup(application.job_queue)
    logger.info("Bot ready: graph compiled, checkpointer at %s", settings.sqlite_path)


async def post_shutdown(application: Application) -> None:
    conn = application.bot_data.get("sqlite_conn")
    if conn is not None:
        await conn.close()


def build_application() -> Application:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")

    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.add_handler(MessageHandler(filters.PHOTO, on_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return application


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    application = build_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
