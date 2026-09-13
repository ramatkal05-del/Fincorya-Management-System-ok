"""
Management command to reset ALL business data while keeping users.

Deletes every row from every business table (cash, operations, finance,
stakeholders, expenses, profits, reports, pricing, audit, notifications)
and resets sequences. Only user-related and Django system tables are kept:

  - auth_user, auth_group, auth_group_permissions, auth_permission
  - django_content_type, django_migrations, django_session
  - accounts_recoverycode, accounts_auththrottle
  - otp_totp_totpdevice

Usage:
    python manage.py reset_all              # interactive confirmation
    python manage.py reset_all --confirm    # skip confirmation
"""
from django.core.management.base import BaseCommand
from django.db import connection


# Tables that are PRESERVED (users + Django internals + user-linked auth).
KEEP_TABLES = {
    "auth_user",
    "auth_group",
    "auth_group_permissions",
    "auth_permission",
    "django_content_type",
    "django_migrations",
    "django_session",
    "django_admin_log",
    "accounts_recoverycode",
    "accounts_auththrottle",
    "otp_totp_totpdevice",
}


class Command(BaseCommand):
    help = (
        "Supprime TOUTES les données métier (caisses, opérations, finances, "
        "parties prenantes, charges, bénéfices, rapports, audit) en gardant "
        "uniquement les comptes utilisateurs et les tables système Django."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Confirmer la réinitialisation sans invite interactive.",
        )

    def handle(self, *args, **options):
        confirmed = options["confirm"]

        if not confirmed:
            self.stdout.write(
                self.style.WARNING(
                    "\nATTENTION — cette opération est IRRÉVERSIBLE.\n"
                    "TOUTES les données métier seront supprimées :\n"
                    "  - Caisses, mouvements, opérations\n"
                    "  - Grand livre financier, écritures, périodes\n"
                    "  - Parties prenantes, garanties, apports\n"
                    "  - Charges, bénéfices, distributions\n"
                    "  - Rapports, audit, notifications\n"
                    "  - Devises, grilles tarifaires, taux de change\n"
                    "Seuls les COMPTES UTILISATEURS sont conservés.\n"
                )
            )
            answer = input("Taper 'oui' pour confirmer : ").strip().lower()
            if answer != "oui":
                self.stdout.write(self.style.ERROR("Annulé."))
                return

        with connection.cursor() as cursor:
            # Get all tables in the public schema.
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            )
            all_tables = [row[0] for row in cursor.fetchall()]

            # Tables to truncate = all tables minus protected ones.
            truncate_tables = [t for t in all_tables if t not in KEEP_TABLES]

            if not truncate_tables:
                self.stdout.write("Aucune table à vider.")
                return

            # Count rows before truncation for reporting.
            total_rows = 0
            for table in truncate_tables:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                row = cursor.fetchone()
                n = row[0] if row else 0
                if n:
                    self.stdout.write(f"  {table}: {n} ligne(s)")
                total_rows += n

            if total_rows == 0:
                self.stdout.write("Toutes les tables sont déjà vides.")
                return

            # TRUNCATE all business tables at once with CASCADE and RESTART IDENTITY.
            # CASCADE handles FK dependencies between business tables automatically.
            # Protected tables (auth_user etc.) are NOT included, so they survive.
            table_list = ", ".join(truncate_tables)
            cursor.execute(f"TRUNCATE {table_list} RESTART IDENTITY CASCADE")

            self.stdout.write(
                self.style.SUCCESS(
                    f"\n{len(truncate_tables)} table(s) vidée(s), "
                    f"{total_rows} ligne(s) supprimée(s)."
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Toutes les données métier ont été supprimées. "
                "Les comptes utilisateurs sont conservés."
            )
        )
