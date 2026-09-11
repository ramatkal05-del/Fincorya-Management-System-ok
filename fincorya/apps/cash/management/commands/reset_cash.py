"""
Management command to reset the entire cash register and operations ledger.

This is an administrator-only destructive tool that bypasses the immutability
protections (ImmutableMovementQuerySet, ServiceOnlyQuerySet, model-level
delete guards) by using raw SQL. It deletes:

  - Cash movements (agent and global)
  - Operations, operation revisions, operation requests
  - Cash handovers, daily closures, cash fundings
  - Finance journal entries and batches (if the ledger tables exist)
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
    #  Helpers                                                            #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _table_exists(cursor, table):
        """Check whether a table exists in the public schema."""
        cursor.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = %s",
            [table],
        )
        return cursor.fetchone() is not None

    def _safe_delete(self, cursor, table):
        """Delete all rows from *table* if it exists; skip silently otherwise."""
        if not self._table_exists(cursor, table):
            return
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        row = cursor.fetchone()
        n = row[0] if row else 0
        if n:
            cursor.execute(f"DELETE FROM {table}")
            self.stdout.write(f"  {table}: {n} ligne(s) supprimée(s)")

    def _safe_update(self, cursor, sql, params=None):
        """Run an UPDATE only if the target table exists."""
        # Extract table name from "UPDATE <table> SET ..."
        parts = sql.strip().split()
        if len(parts) >= 2 and parts[0].upper() == "UPDATE":
            table = parts[1]
            if not self._table_exists(cursor, table):
                return
        cursor.execute(sql, params or [])

    def _reset_balances(self, cursor):
        """Reset cash account and global cash account balances to zero."""
        self._safe_update(cursor, "UPDATE cash_cashaccount SET balance = 0")
        self._safe_update(cursor, "UPDATE cash_globalcashaccount SET balance = 0")
        self._safe_update(cursor, "UPDATE finance_financialaccount SET cached_balance = 0")

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
                self._safe_delete(cursor, table)

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
            # Dynamically find ALL tables with a RESTRICT FK to auth_user.
            # This is more robust than a hardcoded list — it automatically
            # handles tables that don't exist yet (e.g. finance ledger).
            cursor.execute(
                """
                SELECT c.relname AS table_name, a.attname AS column_name
                FROM   pg_constraint k
                JOIN   pg_class c       ON c.oid = k.conrelid
                JOIN   pg_class r      ON r.oid = k.confrelid
                JOIN   pg_attribute a   ON a.attrelid = k.conrelid
                                        AND a.attnum = ANY(k.conkey)
                WHERE  k.contype = 'f'
                  AND  r.relname = 'auth_user'
                  AND  k.confdeltype = 'r'
                """
            )
            restrict_refs = cursor.fetchall()

            for table_name, col_name in restrict_refs:
                cursor.execute(
                    f"DELETE FROM {table_name} WHERE {col_name} = %s",
                    [uid],
                )

        # Now delete the user (CASCADE will clean up SET_NULL and CASCADE FKs)
        user.delete()
        self.stdout.write(
            self.style.SUCCESS(f"Utilisateur '{email}' supprimé.")
        )
