"""Run notification delivery (and the weekly reminder check) inside the
existing web process, as a daemon thread.

This exists so a single-service Render plan (no paid Worker/Cron add-on)
can still deliver notifications: enable it by setting
`RUN_INLINE_NOTIFICATION_WORKER=True` on the web service's environment. If a
dedicated Worker/Cron service is affordable later, disable this and use
those instead (see render.yaml) — running both at once is harmless (DB-level
locking and event_key dedup make concurrent processing safe) but wasteful.
"""
import logging
import threading
import time

logger = logging.getLogger(__name__)

_started = False
_lock = threading.Lock()
REMINDER_CHECK_INTERVAL_SECONDS = 15 * 60  # matches the */15 cron cadence


def start_inline_worker():
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, name="fincorya-inline-notifications", daemon=True).start()
    logger.info("Worker de notifications inline demarre dans ce processus web.")


def _loop():
    from django.core.management import call_command
    from apps.notifications.management.commands.notification_worker import Command as WorkerCommand

    worker = WorkerCommand()
    last_reminder_check = 0.0
    while True:
        try:
            processed = worker.process_batch()
        except Exception:
            logger.exception("Erreur dans le worker de notifications inline.")
            processed = 0
        now = time.monotonic()
        if now - last_reminder_check >= REMINDER_CHECK_INTERVAL_SECONDS:
            last_reminder_check = now
            try:
                call_command("send_weekly_agent_reminders")
            except Exception:
                logger.exception("Erreur lors du rappel hebdomadaire (inline).")
        time.sleep(2 if processed else 8)
