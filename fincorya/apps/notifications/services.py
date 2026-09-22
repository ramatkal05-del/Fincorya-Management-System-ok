from django.db import IntegrityError, transaction
from django.utils import timezone

from .emailing import render_email
from .models import Notification, NotificationCategory, NotificationDelivery


def notify(*, recipient, subject, body=None, level="INFO", email=True, category=NotificationCategory.GENERAL,
           title=None, paragraphs=None, facts=None, cta_label="", cta_url="", footer_note="", event_key=None):
    """Persist first; an email failure must never roll back financial work.

    Backward compatible with the historical 2-line call sites
    (`notify(recipient=..., subject=..., body=..., level=...)`): when only
    `body` is given, the branded template still renders with that single
    paragraph. `event_key` makes the call idempotent per real-world event —
    a scheduler retry or a duplicate trigger reuses the existing row instead
    of sending twice.
    """
    if event_key:
        existing = Notification.objects.filter(event_key=event_key).first()
        if existing:
            return existing

    html_body = ""
    if category != NotificationCategory.GENERAL or paragraphs or facts or cta_url:
        html_body, body = render_email(
            subject=subject, title=title or subject, paragraphs=paragraphs or [body],
            facts=facts, cta_label=cta_label, cta_url=cta_url, footer_note=footer_note,
        )

    try:
        with transaction.atomic():
            notification = Notification.objects.create(
                recipient=recipient, subject=subject, body=body, html_body=html_body,
                category=category, level=level, cta_label=cta_label, cta_url=cta_url,
                event_key=event_key,
            )
            if email and recipient.is_active and recipient.email:
                NotificationDelivery.objects.create(notification=notification, next_attempt_at=timezone.now())
    except IntegrityError:
        # Concurrent sender won the race on the same event_key.
        return Notification.objects.get(event_key=event_key)
    return notification


def resend_failed(delivery_ids=None):
    """Re-queue FAILED deliveries for another attempt, reusing the same row
    (and therefore the same underlying Notification) so no duplicate is
    created for the recipient."""
    qs = NotificationDelivery.objects.filter(status="FAILED")
    if delivery_ids:
        qs = qs.filter(pk__in=delivery_ids)
    return qs.update(status="RETRY", next_attempt_at=timezone.now(), last_error="")
