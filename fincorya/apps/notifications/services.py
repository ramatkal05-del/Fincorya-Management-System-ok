from django.utils import timezone
from .models import Notification, NotificationDelivery


def notify(*, recipient, subject, body, level="INFO", email=True):
    """Persist first; an email failure must never roll back financial work."""
    notification = Notification.objects.create(recipient=recipient, subject=subject, body=body, level=level)
    if email and recipient.email:
        NotificationDelivery.objects.create(notification=notification, next_attempt_at=timezone.now())
    return notification
