"""Send the "closure in N hours" reminder to active agents.

Meant to run frequently (e.g. every 15 minutes) from a scheduler/cron. It is
a no-op outside the configured reminder window, and idempotent per agent per
closure date (`event_key`), so running it twice in the same window — or
after a scheduler restart — never sends a duplicate.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.notifications.models import NotificationSettings
from apps.notifications.senders import send_weekly_agent_reminder


class Command(BaseCommand):
    help = "Envoie le rappel de clôture hebdomadaire aux agents actifs, N heures avant l'heure configurée."

    def add_arguments(self, parser):
        parser.add_argument("--window-minutes", type=int, default=15,
                             help="Tolérance (minutes) autour de l'instant exact 'clôture - N heures'.")

    def handle(self, *args, **options):
        config = NotificationSettings.load()
        if not config.enable_weekly_agent_reminder:
            self.stdout.write("Rappel hebdomadaire agents désactivé — aucune action.")
            return
        zone = ZoneInfo(settings.BUSINESS_TIME_ZONE)
        now_local = timezone.localtime(timezone.now(), zone)
        closure_at = self._next_or_current_closure(now_local, config, zone)
        reminder_at = closure_at - timedelta(hours=config.reminder_hours_before)
        window = timedelta(minutes=options["window_minutes"])
        if not (reminder_at - window <= now_local <= reminder_at + window):
            self.stdout.write(f"Hors fenêtre de rappel (prochaine clôture : {closure_at}, rappel prévu : {reminder_at}).")
            return
        agents = User.objects.filter(role=Role.AGENT, is_active=True)
        sent = 0
        for agent in agents:
            if send_weekly_agent_reminder(agent=agent, closure_at=closure_at):
                sent += 1
        self.stdout.write(self.style.SUCCESS(f"Rappel hebdomadaire : {sent} agent(s) notifié(s) pour la clôture du {closure_at}."))

    @staticmethod
    def _next_or_current_closure(now_local, config, zone):
        """Closest closure datetime (this week or next) to `now_local`."""
        candidates = []
        for week_offset in (-1, 0, 1):
            monday = now_local.date() - timedelta(days=now_local.weekday()) + timedelta(weeks=week_offset)
            closure_date = monday + timedelta(days=config.weekly_closure_weekday)
            candidates.append(datetime.combine(closure_date, config.weekly_closure_time, tzinfo=zone))
        return min(candidates, key=lambda dt: abs((dt - now_local).total_seconds()))
