from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.models import User
from apps.finance.services import migrate_legacy_opening_balances


class Command(BaseCommand):
    help = "Simule ou applique la reprise contrôlée des soldes legacy vers le grand livre finance."

    def add_arguments(self, parser):
        parser.add_argument("--cutover-at", required=True, help="Date de bascule ISO 8601 explicite")
        parser.add_argument("--actor-email", required=True)
        parser.add_argument("--apply", action="store_true", help="Appliquer après simulation validée")
        parser.add_argument("--expected-hash", default="", help="Empreinte de la simulation revue")

    def handle(self, *args, **options):
        try:
            cutover_at = datetime.fromisoformat(options["cutover_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise CommandError("--cutover-at doit être une date/heure ISO 8601 valide") from exc
        if timezone.is_naive(cutover_at):
            raise CommandError("La date doit contenir un fuseau explicite (ex. +03:00).")
        try:
            actor = User.objects.get(email__iexact=options["actor_email"])
        except User.DoesNotExist as exc:
            raise CommandError("Utilisateur initiateur introuvable") from exc
        from django.core.exceptions import ValidationError
        try:
            result = migrate_legacy_opening_balances(actor=actor, cutover_at=cutover_at, apply=options["apply"], expected_hash=options["expected_hash"])
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc
        if options["apply"]:
            self.stdout.write(self.style.SUCCESS(f"Reprise appliquée: {result.public_id}"))
            for row in result.reconciliations.select_related("account", "currency"):
                marker = "OK" if row.difference == 0 and not row.anomalies else "ANOMALIE"
                self.stdout.write(f"{marker} {row.account.code} {row.currency.code}: legacy={row.legacy_balance} repris={row.migrated_balance} différence={row.difference}")
        else:
            self.stdout.write("SIMULATION — aucune écriture créée")
            self.stdout.write(f"Empreinte: {result['preview_hash']}")
            self.stdout.write(f"Lignes d’import non résolues: {result['unresolved_import_rows']}")
            for row in result["accounts"]:
                marker = "ANOMALIE" if row["anomalies"] else "OK"
                self.stdout.write(f"{marker} legacy#{row['legacy_id']} {row['currency']}: {row['legacy_balance']} {'; '.join(row['anomalies'])}")
