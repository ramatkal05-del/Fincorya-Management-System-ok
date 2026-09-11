"""
Management command to reset the entire cash register and operations ledger.

This is an administrator-only destructive tool that bypasses the immutability
protections (ImmutableMovementQuerySet, ServiceOnlyQuerySet, model-level
delete guards) by using raw SQL. It deletes:

  - Cash movements (agent and global)
  - Operations, operation revisions, operation requests
  - Cash handovers, daily closures, cash fundings
  - Finance journal entries and batches
  - Commission conversions
  - Financial-account cached balances are reset to zero

Cash accounts and global cash accounts are kept (their structure is preserved)
but their balances are reset to zero.

Usage:
    python manage.py reset_cash              # interactive confirmation
    python manage.py reset_cash --confirm    # skip confirmation
    python manage.py reset_cash --confirm --delete-user ruthngomo45@gmail.com
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = (
        "Réinitialise complètement la caisse globale, les caisses agents, "
        "les opérations et le grand livre financier. Toutes les écritures "
        "sont supprimées et les soldes remis à zéro."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Confirmer la réinitialisation sans invite interactive.",
        )
        parser.add_argument(
            "--delete-user",
            metavar="EMAIL",
            default="",
            help="Supprimer également le compte utilisateur indiqué après la réinitialisation.",
        )

    # ------------------------------------------------------------------ #
    #  Raw-SQL helpers — bypass ORM immutability guards                    #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _truncate(cursor, table):
        cursor.execute(f"DELETE FROM {table}")

    def _reset_balances(self, cursor):
        """Reset cash account and global cash account balances to zero."""
        cursor.execute("UPDATE cash_cashaccount SET balance = 0")
        cursor.execute("UPDATE cash_globalcashaccount SET balance = 0")
        cursor.execute("UPDATE finance_financialaccount SET cached_balance = 0")

    # ------------------------------------------------------------------ #
    #  Main handler                                                       #
    # ------------------------------------------------------------------ #
    def handle(self, *args, **options):
        confirmed = options["confirm"]
        delete_user_email = options["delete_user"].strip().lower()

        if not confirmed:
            self.stdout.write(
                self.style.WARNING(
                    "\nATTENTION — cette opération est IRRÉVERSIBLE.\n"
                    "Toutes les écritures de caisse, opérations, remises, "
                    "clôtures et écritures du grand livre financier seront "
                    "supprimées. Les soldes seront remis à zéro.\n"
                )
            )
            answer = input("Taper 'oui' pour confirmer : ").strip().lower()
            if answer != "oui":
                self.stdout.write(self.style.ERROR("Annulé."))
                return

        # Deletion order — children first, parents last, to satisfy
        # RESTRICT foreign-key constraints.
        sql_order = [
            # Immutable ledgers (raw SQL bypasses ImmutableMovementQuerySet)
            "cash_cashmovement",
            "cash_globalcashmovement",
            # Operation audit trail (immutable)
            "operations_operationrevision",
            # Idempotency reservations
            "operations_operationrequest",
            # Cash handovers, closures, fundings
            "cash_cashhandover",
            "cash_dailyclosure",
            "cash_cashfunding",
            # Finance: commission conversions reference operations
            "finance_commissionconversion",
            # Finance: journal entries reference batches
            "finance_journalentry",
            # Finance: journal batches
            "finance_journalbatch",
            # Operations (references User RESTRICT, CashAccount RESTRICT)
            "operations_operation",
        ]

        with connection.cursor() as cursor:
            for table in sql_order:
                count_before = cursor.execute(f"SELECT COUNT(*) FROM {table}")
                row = cursor.fetchone()
                n = row[0] if row else 0
                if n:
                    self._truncate(cursor, table)
                    self.stdout.write(f"  {table}: {n} ligne(s) supprimée(s)")

            # Reset balances
            self._reset_balances(cursor)
            self.stdout.write(self.style.SUCCESS("Soldes remis à zéro."))

        self.stdout.write(self.style.SUCCESS("Caisse réinitialisée."))

        # Optional user deletion
        if delete_user_email:
            self._delete_user(delete_user_email)

    def _delete_user(self, email):
        """Delete a user after cleaning up all RESTRICT foreign-key references."""
        from apps.accounts.models import User

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(f"Utilisateur '{email}' introuvable.")
            )
            return

        if user.is_superuser:
            self.stdout.write(
                self.style.WARNING(
                    f"'{email}' est un superadministrateur — "
                    "suppression bloquée par sécurité."
                )
            )
            return

        uid = user.pk
        self.stdout.write(
            f"Suppression des références RESTRICT vers {email} (#{uid})…"
        )

        with connection.cursor() as cursor:
            # Tables with RESTRICT FK to auth_user that would block deletion.
            # Most cash/operation tables are already empty from the reset,
            # but finance/stakeholder/report tables may still hold rows.
            restrict_tables = [
                "finance_journalbatch",        # created_by, posted_by
                "finance_financialperiod",      # locked_by
                "finance_financialaccount",     # responsible_user
                "finance_financecutover",       # requested_by, reviewed_by, approved_by
                "finance_legacymigrationrun",   # created_by
                "finance_stakeholderrule",      # created_by
                "finance_stakeholdercontribution",  # created_by
                "finance_distributionpolicy",   # created_by
                "finance_partnercommissionchoice",  # chosen_by
                "finance_partnerguarantee",     # updated_by
                "finance_remunerationterms",    # updated_by
                "finance_currencyconversion",   # created_by
                "finance_profitdistributionrequest",  # submitted_by, decided_by
                "reports_reportrequest",        # requested_by
                "cash_globalcashaccount",       # administrator
                "operations_operationrequest",  # actor
            ]
            for table in restrict_tables:
                # Delete rows where any user FK column points to this user.
                # We use a dynamic approach: find all FK columns referencing
                # auth_user for each table.
                cursor.execute(
                    """
                    SELECT a.attname
                    FROM   pg_constraint k
                    JOIN   pg_attribute a
                           ON a.attrelid = k.conrelid
                          AND a.attnum   = ANY(k.conkey)
                    WHERE  k.contype = 'f'
                      AND  k.confrelid = (
                          SELECT oid FROM pg_class WHERE relname = 'auth_user'
                      )
                      AND  k.conrelid = (
                          SELECT oid FROM pg_class WHERE relname = %s
                      )
                    """,
                    [table],
                )
                fk_cols = [r[0] for r in cursor.fetchall()]
                for col in fk_cols:
                    cursor.execute(
                        f"DELETE FROM {table} WHERE {col} = %s",
                        [uid],
                    )

        # Now delete the user (CASCADE will clean up SET_NULL and CASCADE FKs)
        user.delete()
        self.stdout.write(
            self.style.SUCCESS(f"Utilisateur '{email}' supprimé.")
        )
