import json
from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings

from apps.audit.models import AuditEvent
from apps.finance.funds import contributed_total, record_contribution
from apps.finance.models import FundContribution, ImportRow
from apps.stakeholders.models import Stakeholder
from tests.test_finance_workflows import FinanceScenario


@override_settings(FINANCE_LEDGER_ENABLED=True, LOCAL_AUTH_BYPASS=False, MFA_ENABLED=False, SECURE_SSL_REDIRECT=False)
class Rapport2CapitalAlignmentTests(FinanceScenario, TestCase):
    def setUp(self):
        super().setUp()
        self.glodie = Stakeholder.objects.create(name="Glodie kongolo", type="SHAREHOLDER")
        self.jenovic = Stakeholder.objects.create(name="JENOVIC mpoto MBOkolo", type="SHAREHOLDER")
        self.joseph = Stakeholder.objects.create(name="Joseph Omari", type="INVESTOR")
        self.fantiny = Stakeholder.objects.create(name="Fantiny", type="PARTNER")
        for party, origin, amount in [
            (self.glodie, "SHAREHOLDER", Decimal("4320.00")),
            (self.jenovic, "SHAREHOLDER", Decimal("1543.00")),
            (self.joseph, "INVESTOR", Decimal("1050.00")),
            (self.fantiny, "PARTNER_GUARANTEE", Decimal("1500.00")),
        ]:
            record_contribution(actor=self.admin, client_key=f"initial-{party.pk}", origin=origin,
                                stakeholder_id=party.pk, amount=amount, currency=self.usd,
                                received_on=date(2026, 9, 1), account_id=self.service_account.pk)
        self.row = ImportRow.objects.create(source_key="RAPPORT-2-OCTOBRE-2026", payload={
            "investments": [],
            "declared_funds": [
                {"stakeholder_id": self.glodie.pk, "name": "Glodie kongolo", "amount": "4370.00", "currency": "USD"},
                {"stakeholder_id": self.jenovic.pk, "name": "JENOVIC mpoto MBOkolo", "amount": "1600.00", "currency": "USD"},
                {"stakeholder_id": self.joseph.pk, "name": "Joseph Omari", "amount": "1090.00", "currency": "USD"},
                {"stakeholder_id": self.fantiny.pk, "name": "Fantiny", "amount": "1500.00", "currency": "USD"},
            ]})

    def run_command(self, **options):
        output = StringIO()
        call_command("apply_rapport2_capital", admin=self.admin.email, stdout=output, **options)
        return json.loads(output.getvalue())

    def test_simulation_reports_deltas_without_writing(self):
        report = self.run_command()
        status = {p["name"]: p["status"] for p in report["parties"]}
        self.assertEqual(status, {"Glodie kongolo": "A_COMPLETER", "JENOVIC mpoto MBOkolo": "A_COMPLETER",
                                  "Joseph Omari": "A_COMPLETER", "Fantiny": "CONFORME"})
        self.assertEqual(FundContribution.objects.filter(external_reference="RAPPORT-2-OCTOBRE-2026").count(), 0)

    def test_apply_records_complements_on_the_same_treasury_account(self):
        report = self.run_command(apply=True)
        status = {p["name"]: p["status"] for p in report["parties"]}
        self.assertEqual(status["Glodie kongolo"], "COMPLEMENT_ENREGISTRE")
        for party, total in [(self.glodie, "4370.00"), (self.jenovic, "1600.00"),
                             (self.joseph, "1090.00"), (self.fantiny, "1500.00")]:
            self.assertEqual(contributed_total(party, self.usd), Decimal(total))
        complement = FundContribution.objects.get(client_key=f"RAPPORT-2-OCTOBRE-2026-CAPITAL-{self.glodie.pk}")
        self.assertEqual((complement.amount, complement.receiving_account_id), (Decimal("50.00"), self.service_account.pk))
        self.row.refresh_from_db()
        self.assertEqual(len(self.row.payload["capital_alignment"]), 4)
        self.assertTrue(AuditEvent.objects.filter(action="RAPPORT2_CAPITAL_ALIGNMENT").exists())

    def test_second_apply_reports_conforme_and_does_not_duplicate(self):
        self.run_command(apply=True)
        second = self.run_command(apply=True)
        status = {p["name"]: p["status"] for p in second["parties"]}
        self.assertEqual(status["Glodie kongolo"], "CONFORME")  # delta désormais nul
        self.assertEqual(FundContribution.objects.filter(external_reference="RAPPORT-2-OCTOBRE-2026").count(), 3)
        self.assertEqual(AuditEvent.objects.filter(action="RAPPORT2_CAPITAL_ALIGNMENT").count(), 1)

    def test_over_recorded_party_is_flagged_not_touched(self):
        payload = self.row.payload
        payload["declared_funds"][3] = {**payload["declared_funds"][3], "amount": "1200.00"}
        self.row.payload = payload
        self.row.save(update_fields=["payload"])
        report = self.run_command(apply=True)
        status = {p["name"]: p["status"] for p in report["parties"]}
        self.assertEqual(status["Fantiny"], "EXCEDENT")
        self.assertEqual(contributed_total(self.fantiny, self.usd), Decimal("1500.00"))

    def test_missing_declared_funds_refuses(self):
        self.row.payload = {"investments": []}
        self.row.save(update_fields=["payload"])
        with self.assertRaises(CommandError):
            self.run_command()
