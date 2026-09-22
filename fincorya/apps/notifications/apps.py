from django.apps import AppConfig
from django.conf import settings


class NotificationsConfig(AppConfig):
    name = "apps.notifications"
    verbose_name = "Notifications"

    def ready(self):
        # Opt-in only: default False everywhere (local dev, tests, release/
        # migrate phase) so nothing changes unless explicitly enabled on the
        # web service's environment. See apps/notifications/background.py.
        if getattr(settings, "RUN_INLINE_NOTIFICATION_WORKER", False):
            from .background import start_inline_worker
            start_inline_worker()
