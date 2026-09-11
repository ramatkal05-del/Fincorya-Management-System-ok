"""Commande de déploiement : crée l'administrateur initial si absent.

Idempotente — ne crée rien si l'admin existe déjà. Le mot de passe doit
être fourni via la variable d'environnement ADMIN_PASSWORD (secret Render).

Utilisation dans le cycle de déploiement :
    python manage.py create_admin
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import Role, User

ADMIN_EMAIL = "fincoryagroup@gmail.com"
ADMIN_FIRST_NAME = "Admin"
ADMIN_LAST_NAME = "FINCORYA"


class Command(BaseCommand):
    help = "Crée l'administrateur initial FINCORYA s'il n'existe pas déjà."

    @transaction.atomic
    def handle(self, *args, **options):
        if User.objects.filter(email=ADMIN_EMAIL).exists():
            self.stdout.write(self.style.WARNING(
                f"L'administrateur {ADMIN_EMAIL} existe déjà — aucune action."
            ))
            return

        password = getattr(settings, "ADMIN_PASSWORD", "")
        if not password:
            raise CommandError("Configurez ADMIN_PASSWORD avant de créer l'administrateur initial.")

        User.objects.create_superuser(
            email=ADMIN_EMAIL,
            password=password,
            first_name=ADMIN_FIRST_NAME,
            last_name=ADMIN_LAST_NAME,
            role=Role.ADMIN,
        )

        self.stdout.write(self.style.SUCCESS(
            f"Administrateur créé : {ADMIN_EMAIL} ({ADMIN_FIRST_NAME} {ADMIN_LAST_NAME})"
        ))
        self.stdout.write(self.style.SUCCESS(
            "Mot de passe appliqué depuis ADMIN_PASSWORD."
        ))
