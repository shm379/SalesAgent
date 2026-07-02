"""Background follow-up jobs: 1h no-reply nudge, 2d no-purchase re-offer with
a discount code. Uses PTB's built-in JobQueue (APScheduler under the hood) -
no need for a second scheduler in this project.

Each new user message cancels and reschedules both jobs, so the countdown
naturally resets. Since run_once jobs only live in memory, reconcile_on_startup
re-derives pending timers from the bot_threads table after a restart."""

from __future__ import annotations

import logging
import random
import string
from datetime import datetime, timezone

from telegram.ext import ContextTypes, JobQueue

from app.bot import threads_store
from app.config import get_settings

logger = logging.getLogger(__name__)


def _nudge_name(chat_id) -> str:
    return f"nudge_{chat_id}"


def _reoffer_name(chat_id) -> str:
    return f"reoffer_{chat_id}"


def _cancel_named(job_queue: JobQueue, name: str) -> None:
    for job in job_queue.get_jobs_by_name(name):
        job.schedule_removal()


def schedule_followups(job_queue: JobQueue, chat_id: int) -> None:
    settings = get_settings()
    _cancel_named(job_queue, _nudge_name(chat_id))
    _cancel_named(job_queue, _reoffer_name(chat_id))

    job_queue.run_once(
        nudge_job,
        when=settings.nudge_delay_seconds,
        chat_id=chat_id,
        name=_nudge_name(chat_id),
        data={"chat_id": chat_id},
    )
    job_queue.run_once(
        reoffer_job,
        when=settings.reoffer_delay_seconds,
        chat_id=chat_id,
        name=_reoffer_name(chat_id),
        data={"chat_id": chat_id},
    )


def cancel_followups(job_queue: JobQueue, chat_id: int) -> None:
    _cancel_named(job_queue, _nudge_name(chat_id))
    _cancel_named(job_queue, _reoffer_name(chat_id))


def _discount_code(chat_id: int) -> str:
    # Deterministic per chat_id so re-sends (e.g. after a restart) are stable
    # instead of generating a new code every time.
    rng = random.Random(str(chat_id))
    return "SALE-" + "".join(rng.choices(string.ascii_uppercase + string.digits, k=6))


async def nudge_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.data["chat_id"]
    settings = get_settings()
    thread = threads_store.get_thread(settings.bot_threads_sqlite_path, str(chat_id))
    if thread is None or thread["purchase_confirmed"]:
        return
    await context.bot.send_message(
        chat_id=chat_id,
        text="سلام! هنوز اونجایی؟ اگه سوالی دربارهٔ محصولاتی که بررسی کردیم داری یا کمک بیشتری لازم داری، من اینجام 🙂",
    )


async def reoffer_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.data["chat_id"]
    settings = get_settings()
    thread = threads_store.get_thread(settings.bot_threads_sqlite_path, str(chat_id))
    if thread is None or thread["purchase_confirmed"]:
        return

    code = thread.get("discount_code") or _discount_code(chat_id)
    threads_store.set_discount_code(settings.bot_threads_sqlite_path, str(chat_id), code)

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "دیدم هنوز خریدت رو نهایی نکردی! برای اینکه تصمیم‌گیری راحت‌تر باشه، "
            f"با کد تخفیف «{code}» می‌تونی از محصولاتی که بررسی کردیم با قیمت بهتر خرید کنی. "
            "اگه سوالی مونده بگو کمکت کنم 🙂"
        ),
    )


async def reconcile_on_startup(job_queue: JobQueue) -> None:
    """Re-derive pending nudge/re-offer timers from bot_threads after a
    restart (JobQueue jobs live only in memory and don't survive one)."""
    settings = get_settings()
    now = datetime.now(timezone.utc)

    for thread in threads_store.get_all_threads(settings.bot_threads_sqlite_path):
        if thread["purchase_confirmed"]:
            continue
        try:
            last = datetime.fromisoformat(thread["last_user_message_at"])
        except ValueError:
            continue
        chat_id = int(thread["chat_id"])
        elapsed = (now - last).total_seconds()

        # If a threshold already passed while the bot was down, fire soon
        # after startup instead of never; otherwise resume the countdown.
        nudge_in = max(settings.nudge_delay_seconds - elapsed, 5)
        reoffer_in = max(settings.reoffer_delay_seconds - elapsed, 10)

        _cancel_named(job_queue, _nudge_name(chat_id))
        _cancel_named(job_queue, _reoffer_name(chat_id))
        job_queue.run_once(nudge_job, when=nudge_in, chat_id=chat_id, name=_nudge_name(chat_id), data={"chat_id": chat_id})
        job_queue.run_once(reoffer_job, when=reoffer_in, chat_id=chat_id, name=_reoffer_name(chat_id), data={"chat_id": chat_id})

        logger.info("Rescheduled chat_id=%s: nudge in %.0fs, reoffer in %.0fs", chat_id, nudge_in, reoffer_in)
