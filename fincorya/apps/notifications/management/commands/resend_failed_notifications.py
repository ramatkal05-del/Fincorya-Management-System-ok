from django.core.management.base import BaseCommand

from apps.notifications.services import resend_failed


class Command(BaseCommand):
    help = "Relance les envois échoués (statut FAILED) sans créer de doublon : réutilise la même ligne de livraison."

    def handle(self, *args, **options):
        count = resend_failed()
        self.stdout.write(self.style.SUCCESS(f"{count} envoi(s) échoué(s) repassé(s) en file pour une nouvelle tentative."))
