import json
from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command, CommandError
from django.test import TestCase

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.finance.models import FinancialAccount, FundContribution, ImportRow
from apps.pricing.models import Currency
from apps.stakeholders.models import Investment, Stakeholder


class Rapport2InvestmentDedupTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(email="admin@example.test", role="ADMIN")
        self.usd = Currency.objects.create(code="USD")
        self.meschack = Stakeholder.objects.create(name="Meschack BUKASA KABANGA", type="SHAREHOLDER")
        self.glodie = Stakeholder.objects.create(name="Glodie kongolo", type="SHAREHOLDER")
        self.capital = FinancialAccount.objects.create(code="CAPITAL-TEST", name="Capital test",
                                                        account_type="CAPITAL", nature="EQUITY", currency=self.usd)
        self.linked_inv = Investment.objects.create(stakeholder=self.meschack, currency=self.usd,
                                                     amount=Decimal("7000.00"), invested_on=date(2026, 10, 1))
        self.phantom_inv = Investment.objects.create(stakeholder=self.glodie, currency=self.usd,
                                                      amount=Decimal("4370.00"), invested_on=date(2026, 10, 1))
        self.contribution = FundContribution.objects.create(
            client_key="contrib-meschack", origin="SHAREHOLDER", stakeholder=self.meschack,
            amount=Decimal("7000.00"), currency=self.usd, received_on=date(2026, 10, 1),
            receiving_account=self.capital, created_by=self.admin)
        self.row = ImportRow.objects.create(
            source_key="RAPPORT-2-OCTOBRE-2026",
            payload={"investments": [self.linked_inv.pk, self.phantom_inv.pk]},
            resolution="Rapport 2 octobre 2026 importé.")

    def run_command(self, **options):
        output = StringIO()
        call_command("dedup_rapport2_investments", admin=self.admin.email, stdout=output, **options)
        return json.loads(output.getvalue())

    def test_simulation_plans_without_touching(self):
        report = self.run_command()
        self.assertEqual(report["mode"], "SIMULATION")
        treatments = {t["investment_id"]: t["status"] for t in report["treatments"]}
        self.assertEqual(treatments, {self.linked_inv.pk: "A_RAPPROCHER", self.phantom_inv.pk: "A_SUPPRIMER"})
        self.assertEqual(Investment.objects.count(), 2)
        self.contribution.refresh_from_db()
        self.assertIsNone(self.contribution.legacy_investment_id)
        self.row.refresh_from_db()
        self.assertEqual(self.row.payload["investments"], [self.linked_inv.pk, self.phantom_inv.pk])

    def test_apply_links_the_exact_match_and_deletes_the_phantom(self):
        report = self.run_command(apply=True)
        treatments = {t["investment_id"]: t["status"] for t in report["treatments"]}
        self.assertEqual(treatments, {self.linked_inv.pk: "RAPPROCHE", self.phantom_inv.pk: "SUPPRIME"})
        self.contribution.refresh_from_db()
        self.assertEqual(self.contribution.legacy_investment_id, self.linked_inv.pk)
        self.assertFalse(Investment.objects.filter(pk=self.phantom_inv.pk).exists())
        self.assertTrue(Investment.objects.filter(pk=self.linked_inv.pk).exists())
        self.row.refresh_from_db()
        self.assertEqual(self.row.payload["investments"], [])
        self.assertEqual(len(self.row.payload["declared_funds"]), 2)
        self.assertEqual(len(self.row.payload["deduplicated_investments"]), 2)
        self.assertTrue(AuditEvent.objects.filter(action="CONTRIBUTION_RECONCILE").exists())
        self.assertTrue(AuditEvent.objects.filter(action="RAPPORT2_INVESTMENT_DEDUP").exists())

    def test_second_apply_is_a_noop(self):
        self.run_command(apply=True)
        self.assertEqual(self.run_command(apply=True)["status"], "DEJA_TRAITE")
        self.assertEqual(AuditEvent.objects.filter(action="RAPPORT2_INVESTMENT_DEDUP").count(), 1)

    def test_unexpected_row_aborts_before_any_change(self):
        self.phantom_inv.amount = Decimal("9999.00")
        self.phantom_inv.save(update_fields=["amount"])
        with self.assertRaises(CommandError):
            self.run_command(apply=True)
        self.assertTrue(Investment.objects.filter(pk=self.phantom_inv.pk).exists())
        self.contribution.refresh_from_db()
        self.assertIsNone(self.contribution.legacy_investment_id)
        self.assertFalse(AuditEvent.objects.filter(action="RAPPORT2_INVESTMENT_DEDUP").exists())

    def test_ambiguous_match_aborts(self):
        FundContribution.objects.create(
            client_key="contrib-meschack-2", origin="SHAREHOLDER", stakeholder=self.meschack,
            amount=Decimal("7000.00"), currency=self.usd, received_on=date(2026, 10, 1),
            receiving_account=self.capital, created_by=self.admin)
        with self.assertRaises(CommandError):
            self.run_command(apply=True)
        self.assertTrue(Investment.objects.filter(pk=self.linked_inv.pk).exists())
