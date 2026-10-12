"""Align the recorded capital on the Rapport 2 declared fund situation.

The declared funds (18 960 USD) are the authoritative capital situation per
the operator; recorded `FundContribution` rows total 18 813. For each
declared party this command records a *complementary* contribution for the
difference, on the same treasury account that received that party's original
apport — DEBIT treasury / CREDIT capital, plus the `CAPITAL_IN` projection
on the legacy cash (`record_contribution`). Parties already matching are
left alone; parties recorded *above* the declaration are flagged, never
touched.

`client_key` and `external_reference` make each complement idempotent: a
second `--apply` reports DEJA_ENREGISTRE instead of duplicating. Simulation
by default; the whole run is one transaction.
"""
import json
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum

from apps.finance.locking import ledger_atomic

SOURCE_KEY = "RAPPORT-2-OCTOBRE-2026"
# Date de régularisation : à l'intérieur du mois d'octobre (ouvert), hors de
# la semaine 1–11 potentiellement verrouillée. Constante pour l'idempotence.
EFFECTIVE_ON = date(2026, 10, 12)

TYPE_TO_ORIGIN = {"SHAREHOLDER": "SHAREHOLDER", "INVESTOR": "INVESTOR", "PARTNER": "PARTNER_GUARANTEE"}


class Command(BaseCommand):
    help = "Enregistre les compléments d'apport pour égaler le capital déclaré du Rapport 2 ; simulation par défaut."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--admin", default="fincoryagroup@gmail.com")

    @ledger_atomic
    def handle(self, *args, **options):
        from apps.accounts.models import User
        from apps.audit.services import record
        from apps.finance.funds import record_contribution
        from apps.finance.models import FundContribution, ImportRow
        from apps.pricing.models import Currency
        from apps.stakeholders.models import Stakeholder

        actor = User.objects.filter(email__iexact=options["admin"], role="ADMIN", is_active=True).first()
        if actor is None:
            raise CommandError("Administrateur actif introuvable ; préciser --admin EMAIL.")
        row = ImportRow.objects.filter(source_key=SOURCE_KEY).first()
        if row is None or not row.payload.get("declared_funds"):
            raise CommandError("declared_funds absent de l'ImportRow ; exécuter d'abord dedup_rapport2_investments --apply.")

        usd = Currency.objects.get(code="USD")
        report = {"mode": "APPLY" if options["apply"] else "SIMULATION", "import_row_id": row.pk, "parties": []}
        for entry in row.payload["declared_funds"]:
            party = Stakeholder.objects.get(pk=entry["stakeholder_id"])
            declared = Decimal(entry["amount"])
            recorded = (FundContribution.objects.filter(stakeholder=party, currency=usd)
                        .aggregate(total=Sum("amount"))["total"] or Decimal("0"))
            delta = declared - recorded
            item = {"stakeholder_id": party.pk, "name": party.name, "declared": str(declared),
                    "recorded": str(recorded), "delta": str(delta)}
            report["parties"].append(item)
            if delta == 0:
                item["status"] = "CONFORME"
                continue
            if delta < 0:
                item["status"] = "EXCEDENT"  # enregistré au-dessus de la déclaration : examen manuel, rien touché
                continue
            client_key = f"{SOURCE_KEY}-CAPITAL-{party.pk}"
            origin = TYPE_TO_ORIGIN.get(party.type)
            if origin is None:
                raise CommandError(f"Type de partie non alignable pour {party.name} ({party.type}).")
            source = (FundContribution.objects.filter(stakeholder=party, currency=usd)
                      .select_related("receiving_account").order_by("received_on", "pk").first())
            if source is None:
                raise CommandError(f"Aucun apport existant pour {party.name} : compte de réception indéterminé, "
                                   "enregistrement manuel requis.")
            item["receiving_account"] = source.receiving_account.code
            if FundContribution.objects.filter(client_key=client_key).exists():
                item["status"] = "DEJA_ENREGISTRE"
                continue
            if options["apply"]:
                contribution = record_contribution(
                    actor=actor, client_key=client_key, origin=origin, stakeholder_id=party.pk,
                    amount=delta, currency=usd, received_on=EFFECTIVE_ON,
                    account_id=source.receiving_account_id, external_reference=SOURCE_KEY,
                    note=f"Complément d'apport : situation déclarée {declared} USD (Rapport 2 octobre) "
                         f"vs {recorded} USD enregistrés.")
                item["status"] = "COMPLEMENT_ENREGISTRE"
                item["contribution_id"] = contribution.pk
            else:
                item["status"] = "A_COMPLETER"

        if options["apply"]:
            changed = any(p["status"] == "COMPLEMENT_ENREGISTRE" for p in report["parties"])
            payload = dict(row.payload)
            payload["capital_alignment"] = report["parties"]
            row.payload = payload
            if changed and "Capital aligné" not in (row.resolution or ""):
                row.resolution = (row.resolution or "") + " Capital aligné sur la situation déclarée (compléments enregistrés)."
            row.save(update_fields=["payload", "resolution"])
            if changed:
                record(actor=actor, action="RAPPORT2_CAPITAL_ALIGNMENT", instance=row,
                       before={"declared_funds": payload["declared_funds"]},
                       after={"parties": report["parties"]})
        report["status"] = "OK"
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        if not options["apply"]:
            transaction.set_rollback(True)
