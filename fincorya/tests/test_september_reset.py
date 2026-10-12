import json
from datetime import date, datetime
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings

from apps.audit.models import AuditEvent
from apps.cash.models import CashFunding, CashHandover, CashMovement, DailyClosure, GlobalCashMovement
from apps.cash.services import adjust_global_cash
from apps.expenses.models import Expense
from apps.finance.funds import contributed_total, record_contribution
from apps.finance.models import FundContribution, ImportRow, JournalBatch, LedgerEntry
from apps.operations.models import Operation
from apps.profits.models import Allocation, Distribution, ProfitPeriod
from apps.stakeholders.models import PartnerOperation, Stakeholder
from tests.test_finance_workflows import ZONE, FinanceScenario

SEPT = datetime(2026, 9, 5, 12, tzinfo=ZONE)
OCT = datetime(2026, 10, 12, 12, tzinfo=ZONE)


@override_settings(FINANCE_LEDGER_ENABLED=True, LOCAL_AUTH_BYPASS=False, MFA_ENABLED=False, SECURE_SSL_REDIRECT=False)
class SeptemberResetTests(FinanceScenario, TestCase):
    def setUp(self):
        super().setUp()
        self.shareholder = Stakeholder.objects.create(name="Shareholder A", type="SHAREHOLDER")
        with patch("django.utils.timezone.now", return_value=SEPT):
            self.sept_operation = self.operation(key="sept-op", partner=self.partner, when=SEPT)
            self.oct_operation = self.operation(key="oct-op", partner=self.partner, when=OCT)
            record_contribution(actor=self.admin, client_key="sept-capital", origin="SHAREHOLDER",
                                stakeholder_id=self.shareholder.pk, amount=Decimal("500"), currency=self.usd,
                                received_on=date(2026, 9, 5), account_id=self.service_account.pk)
            adjust_global_cash(global_account_id=self.global_cash.pk, direction="IN", amount=Decimal("100"),
                               adjusted_by=self.admin, note="Sept capital top-up")
            CashFunding.objects.create(account=self.legacy, amount=Decimal("50"), allocated_by=self.admin)
            CashHandover.objects.create(account=self.legacy, amount=Decimal("20"), requested_by=self.admin)
        DailyClosure.objects.create(account=self.legacy, business_date=date(2026, 9, 10),
                                    opening_balance=Decimal("0"), status="CLOSED")
        Expense.objects.create(label="Sept rent", amount=Decimal("100"), currency=self.usd,
                               incurred_on=date(2026, 9, 10), created_by=self.admin)
        self.profit = ProfitPeriod.objects.create(start_date=date(2026, 9, 1), end_date=date(2026, 9, 6),
                                                  currency=self.usd, status="FINALIZED", calculated_by=self.admin)
        allocation = Allocation.objects.create(period=self.profit, bucket="shareholders",
                                               percentage=Decimal("100"), amount=Decimal("50"))
        Distribution.objects.create(allocation=allocation, stakeholder=self.shareholder,
                                    amount=Decimal("50"), status="PAID")

    def run_command(self, **options):
        output = StringIO()
        call_command("reset_september_activity", admin=self.admin.email, stdout=output, **options)
        return json.loads(output.getvalue())

    def test_apply_requires_confirm_token(self):
        with self.assertRaises(CommandError):
            call_command("reset_september_activity", apply=True, admin=self.admin.email, stdout=StringIO())
        self.assertTrue(Operation.objects.filter(pk=self.sept_operation.pk).exists())

    def test_simulation_invents_nothing(self):
        report = self.run_command()
        self.assertGreaterEqual(report["deleted"]["operations"], 1)
        self.assertGreaterEqual(report["deleted"]["cash_movements"], 1)
        self.assertTrue(Operation.objects.filter(pk=self.sept_operation.pk).exists())
        self.assertTrue(Expense.objects.filter(label="Sept rent").exists())

    def test_apply_erases_september_keeps_capital_and_october(self):
        from config.business_time import business_day_bounds
        lower, upper = business_day_bounds(date(2026, 9, 1), date(2026, 9, 30))
        sept_batch = JournalBatch.objects.get(event_type="OPERATION", effective_at__lt=upper)
        report = self.run_command(apply=True, confirm="RESET-SEPTEMBRE")

        self.assertFalse(Operation.objects.filter(pk=self.sept_operation.pk).exists())
        self.assertFalse(PartnerOperation.objects.filter(operation_id=self.sept_operation.pk).exists())
        self.assertEqual(PartnerOperation.objects.count(), 1)  # l'opération d'octobre garde la sienne
        self.assertFalse(CashMovement.objects.filter(created_at__gte=lower, created_at__lt=upper).exists())
        self.assertTrue(CashMovement.objects.filter(created_at__lt=lower).exists())   # juin conservé
        self.assertTrue(CashMovement.objects.filter(created_at__gte=upper).exists())  # octobre conservé
        self.assertTrue(GlobalCashMovement.objects.filter(created_at__lt=lower).exists())
        self.assertFalse(CashFunding.objects.filter(created_at__gte=lower, created_at__lt=upper).exists())
        self.assertEqual(CashFunding.objects.count(), 1)  # allocation de juin conservée
        for model in (CashHandover, DailyClosure, Expense, ProfitPeriod, Allocation, Distribution):
            self.assertEqual(model.objects.count(), 0, model.__name__)
        self.assertFalse(JournalBatch.objects.filter(event_type="OPERATION", effective_at__lt=upper).exists())
        self.assertEqual(JournalBatch.objects.filter(event_type="OPERATION").count(), 1)  # octobre conservé
        self.assertTrue(JournalBatch.objects.filter(event_type="CONTRIBUTION").exists())
        self.assertTrue(JournalBatch.objects.filter(event_type="LEGACY_OPENING").exists())
        self.assertFalse(LedgerEntry.objects.filter(batch_id=sept_batch.pk).exists())
        self.assertTrue(Operation.objects.filter(pk=self.oct_operation.pk).exists())
        self.assertTrue(FundContribution.objects.exists())
        self.assertEqual(contributed_total(self.shareholder, self.usd), Decimal("500"))
        self.assertTrue(ImportRow.objects.filter(source_key="RESET-SEPTEMBRE-2026").exists())
        self.assertTrue(AuditEvent.objects.filter(action="SEPTEMBER_ACTIVITY_RESET").exists())
        self.assertEqual(report["kept"]["operations_october_and_after"], 1)

    def test_second_apply_reports_done(self):
        self.run_command(apply=True, confirm="RESET-SEPTEMBRE")
        self.assertEqual(self.run_command(apply=True, confirm="RESET-SEPTEMBRE")["status"], "DEJA_TRAITE")
        self.assertEqual(AuditEvent.objects.filter(action="SEPTEMBER_ACTIVITY_RESET").count(), 1)
