"""Deduplicate the six legacy receipts the Rapport 2 import wrongly created.

The first run of record_rapport2_octobre.py registered the six *declared*
fund positions as new `Investment` rows (a cumulative situation already
recorded as `FundContribution`, not six new payments). This command repairs
that state per row, using the ImportRow payload as the authoritative list:

  - exact same contribution already recorded (party, currency, amount and
    received date identical) -> linked via `link_legacy_receipt`, the
    system's native deduplication path;
  - no corresponding contribution -> deleted : the row records a payment
    that never happened; the declared figure stays in the ImportRow payload
    and in the audit trail;
  - several matching contributions -> aborts, manual arbitration required.

The ImportRow is then rewritten to the shape the corrected script produces
(`investments: []` + `declared_funds`), keeping a snapshot of every removed
row under `deduplicated_investments`. Simulation by default, `--apply` to
execute; the whole run is one transaction.
"""
import json
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.finance.locking import ledger_atomic

SOURCE_KEY = "RAPPORT-2-OCTOBRE-2026"
DECLARED_ON = date(2026, 10, 1)

# Fonds déclarés par le rapport 2 (situation cumulée, pas des versements).
EXPECTED = {
    "Meschack BUKASA KABANGA": Decimal("7000.00"),
    "Glodie kongolo": Decimal("4370.00"),
    "Ruth Ngomo": Decimal("3400.00"),
    "JENOVIC mpoto MBOkolo": Decimal("1600.00"),
    "Fantiny": Decimal("1500.00"),
    "Joseph Omari": Decimal("1090.00"),
}

LINK_JUSTIFICATION = ("Rapport 2 : la déclaration reprend un apport déjà "
                      "enregistré ; ce n'est pas un nouveau versement.")


class Command(BaseCommand):
    help = "Rapproche ou retire les versements dupliqués du Rapport 2 ; simulation par défaut."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--admin", default="fincoryagroup@gmail.com")

    @ledger_atomic
    def handle(self, *args, **options):
        from apps.accounts.models import User
        from apps.audit.services import record
        from apps.finance.funds import link_legacy_receipt
        from apps.finance.models import FundContribution, ImportRow
        from apps.stakeholders.models import Investment

        actor = User.objects.filter(email__iexact=options["admin"], role="ADMIN", is_active=True).first()
        if actor is None:
            raise CommandError("Administrateur actif introuvable ; préciser --admin EMAIL.")
        row = ImportRow.objects.filter(source_key=SOURCE_KEY).first()
        if row is None:
            raise CommandError(f"ImportRow {SOURCE_KEY} introuvable.")

        investment_ids = row.payload.get("investments") or []
        report = {"mode": "APPLY" if options["apply"] else "SIMULATION",
                  "import_row_id": row.pk, "treatments": []}
        if not investment_ids:
            report["status"] = "DEJA_TRAITE"
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            return

        declared_funds, snapshots = [], []
        for pk in investment_ids:
            inv = Investment.objects.select_for_update().filter(pk=pk).first()
            if inv is None:
                report["treatments"].append({"investment_id": pk, "status": "ABSENT"})
                continue
            name = inv.stakeholder.name
            if EXPECTED.get(name) != inv.amount or inv.currency.code != "USD" or inv.invested_on != DECLARED_ON:
                raise CommandError(f"Versement #{pk} inattendu ({name} {inv.amount} {inv.invested_on}) ; "
                                   "vérification manuelle requise avant toute modification.")
            snapshot = {"investment_id": inv.pk, "stakeholder_id": inv.stakeholder_id, "name": name,
                        "amount": str(inv.amount), "currency": inv.currency.code,
                        "invested_on": str(inv.invested_on)}
            snapshots.append(snapshot)
            declared_funds.append({"stakeholder_id": inv.stakeholder_id, "name": name,
                                   "amount": str(inv.amount), "currency": "USD"})
            if hasattr(inv, "received_contribution"):
                report["treatments"].append({**snapshot, "status": "DEJA_RAPPROCHE",
                                             "contribution_id": inv.received_contribution.pk})
                continue
            candidates = list(FundContribution.objects.filter(
                legacy_investment__isnull=True, stakeholder_id=inv.stakeholder_id,
                currency_id=inv.currency_id, amount=inv.amount, received_on=inv.invested_on))
            if len(candidates) > 1:
                raise CommandError(f"Plusieurs apports candidats pour le versement #{pk} ; arbitrage manuel requis.")
            if options["apply"]:
                if candidates:
                    link_legacy_receipt(actor=actor, contribution_id=candidates[0].pk, investment_id=inv.pk,
                                        justification=LINK_JUSTIFICATION)
                    status = "RAPPROCHE"
                else:
                    inv.delete()
                    status = "SUPPRIME"
            else:
                status = "A_RAPPROCHER" if candidates else "A_SUPPRIMER"
            report["treatments"].append({**snapshot, "status": status,
                                         "contribution_id": candidates[0].pk if candidates else None})

        if options["apply"]:
            payload = dict(row.payload)
            payload["investments"] = []
            payload["declared_funds"] = declared_funds
            payload["deduplicated_investments"] = snapshots
            row.payload = payload
            row.resolution = (row.resolution or "") + " Versements déclarés dédupliqués post-import."
            row.save(update_fields=["payload", "resolution"])
            record(actor=actor, action="RAPPORT2_INVESTMENT_DEDUP", instance=row,
                   before={"investment_ids": investment_ids},
                   after={"treatments": report["treatments"]})
        report["status"] = "OK"
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        if not options["apply"]:
            transaction.set_rollback(True)
