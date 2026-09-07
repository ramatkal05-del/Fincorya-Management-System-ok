import time
from datetime import timedelta
from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from apps.notifications.models import NotificationDelivery


class Command(BaseCommand):
    help = "Traite la file persistante des notifications e-mail FINCORYA."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        while True:
            processed = self.process_batch()
            if options["once"]:
                return
            time.sleep(2 if processed else 8)

    def process_batch(self):
        NotificationDelivery.objects.filter(
            status="PROCESSING", updated_at__lt=timezone.now() - timedelta(minutes=15)
        ).update(status="RETRY", next_attempt_at=timezone.now())
        processed = 0
        for delivery_id in NotificationDelivery.objects.filter(status__in=["PENDING", "RETRY"], next_attempt_at__lte=timezone.now()).values_list("id", flat=True)[:20]:
            self.process_one(delivery_id)
            processed += 1
        return processed

    def process_one(self, delivery_id):
        with transaction.atomic():
            delivery = NotificationDelivery.objects.select_for_update(skip_locked=True).select_related("notification__recipient").filter(pk=delivery_id).first()
            if not delivery or delivery.status not in {"PENDING", "RETRY"}:
                return
            delivery.status = "PROCESSING"
            delivery.attempts += 1
            delivery.save(update_fields=["status", "attempts", "updated_at"])
            subject = delivery.notification.subject
            body = delivery.notification.body
            recipient = delivery.notification.recipient.email
        try:
            send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient], fail_silently=False)
        except Exception as exc:
            error = str(exc)[:2000]
            sent = False
        else:
            error = ""
            sent = True
        with transaction.atomic():
            delivery = NotificationDelivery.objects.select_for_update().get(pk=delivery_id)
            delivery.last_error = error
            if sent:
                delivery.status = "SENT"
                delivery.sent_at = timezone.now()
            else:
                delivery.status = "FAILED" if delivery.attempts >= 5 else "RETRY"
                delivery.next_attempt_at = timezone.now() + timedelta(minutes=min(60, 2 ** delivery.attempts))
            delivery.save(update_fields=["last_error", "status", "next_attempt_at", "sent_at", "updated_at"])
