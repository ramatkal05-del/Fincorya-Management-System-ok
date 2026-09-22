"""Lightweight in-process scheduler for deployments without a native cron
service (Render's `cron` service type is preferred there — see render.yaml).
Loops forever, calling `send_weekly_agent_reminders` every N minutes."""
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Boucle de planification FINCORYA (rappel hebdomadaire agents). Prévoir un cron natif en production si possible."

    def add_arguments(self, parser):
        parser.add_argument("--interval-seconds", type=int, default=900)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        while True:
            try:
                call_command("send_weekly_agent_reminders")
            except Exception as exc:  # never let a scheduling error kill the loop
                self.stderr.write(self.style.ERROR(str(exc)))
            if options["once"]:
                return
            time.sleep(options["interval_seconds"])
