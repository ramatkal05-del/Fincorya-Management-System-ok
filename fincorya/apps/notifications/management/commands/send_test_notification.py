"""Queue a real test e-mail to a given address, through the normal
persist-then-deliver pipeline (so it also exercises the worker/backend)."""
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.notifications.emailing import absolute_url
from apps.notifications.models import NotificationCategory
from apps.notifications.services import notify


class Command(BaseCommand):
    help = "Envoie un e-mail de test FINCORYA à l'adresse d'un utilisateur existant."

    def add_arguments(self, parser):
        parser.add_argument("email", help="Adresse e-mail d'un utilisateur existant du système.")

    def handle(self, *args, **options):
        try:
            user = User.objects.get(email__iexact=options["email"])
        except User.DoesNotExist:
            raise CommandError("Aucun utilisateur avec cette adresse ; créez d'abord le compte de test.")
        notify(
            recipient=user, category=NotificationCategory.TEST,
            subject="E-mail de test FINCORYA", title="Ceci est un test",
            paragraphs=["Cet e-mail confirme que la configuration d'envoi FINCORYA (identité visuelle, "
                         "file d'attente, worker) fonctionne correctement."],
            cta_label="Ouvrir FINCORYA", cta_url=absolute_url("/"),
        )
        self.stdout.write(self.style.SUCCESS(
            f"Test mis en file pour {user.email}. Lancez `manage.py notification_worker --once` pour l'envoyer."
        ))
