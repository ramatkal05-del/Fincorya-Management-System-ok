from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.audit.models import AuditEvent
from apps.cash.models import CashAccount, CashMovement, GlobalCashAccount, GlobalCashMovement, MovementDirection
from apps.cash.models import ClosureStatus, DailyClosure
from apps.cash.services import adjust_global_cash, allocate_cash, close_cash_day, confirm_handover, handover_cash
from apps.expenses.models import ApprovalDecision, Expense, ExpenseStatus
from apps.expenses.services import decide_expense
from apps.operations.models import OperationStatus
from apps.operations.services import cancel_operation, create_sent_transfer, create_withdrawal, pay_received_transfer, receive_transfer, revise_operation
from apps.pricing.models import Currency, ExchangeRate, TariffSchedule, TariffTier
from apps.pricing.services import lookup_fee, resolve_fee
from apps.profits.models import ProfitPeriod
from apps.profits.services import calculate_profit_period
from apps.reports.services import generate_monthly_report, generate_operation_report, monthly_financial_snapshot


class FincoryaServiceTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.agent = User.objects.create_user(email="agent@fincorya.test", password="test", role=Role.AGENT)
        cls.admin = User.objects.create_user(email="admin@fincorya.test", password="test", role=Role.ADMIN)
        cls.partner = User.objects.create_user(email="partner@fincorya.test", password="test", role=Role.PARTNER)
        cls.usd = Currency.objects.create(code="USD")
        cls.global_cash = GlobalCashAccount.objects.create(administrator=cls.admin, currency=cls.usd)
        ExchangeRate.objects.create(currency=cls.usd, rate_to_usd=Decimal("1"), effective_at=timezone.now(), created_by=cls.admin)
        cls.schedule = TariffSchedule.objects.create(name="Standard USD", currency=cls.usd, is_published=True)
        TariffTier.objects.create(schedule=cls.schedule, min_amount=Decimal("0.01"), max_amount=Decimal("40.00"), fixed_fee=Decimal("5.00"))
        TariffTier.objects.create(schedule=cls.schedule, min_amount=Decimal("40.01"), max_amount=Decimal("100.00"), fixed_fee=Decimal("8.00"))
        TariffTier.objects.create(schedule=cls.schedule, min_amount=Decimal("100.01"), max_amount=Decimal("5000.00"), fixed_fee=Decimal("15.00"))
        cls.account = CashAccount.objects.create(agent=cls.agent, currency=cls.usd, global_account=cls.global_cash)
        adjust_global_cash(global_account_id=cls.global_cash.pk, direction=MovementDirection.IN, amount=Decimal("101000.00"), adjusted_by=cls.admin, note="Capital de test")
        allocate_cash(account_id=cls.account.pk, amount=Decimal("1000.00"), allocated_by=cls.admin, note="Allocation de test")
        cls.global_cash.refresh_from_db()
        cls.account.refresh_from_db()

    def test_tariff_boundaries_and_manual_reduction(self):
        self.assertEqual(lookup_fee(self.schedule, Decimal("0.01")), Decimal("5.00"))
        self.assertEqual(lookup_fee(self.schedule, Decimal("40.00")), Decimal("5.00"))
        self.assertEqual(lookup_fee(self.schedule, Decimal("40.01")), Decimal("8.00"))
        self.assertEqual(lookup_fee(self.schedule, Decimal("5000.00")), Decimal("15.00"))
        self.assertEqual(resolve_fee(self.schedule, Decimal("200"), Decimal("3")), Decimal("3"))
        with self.assertRaises(ValidationError):
            resolve_fee(self.schedule, Decimal("200"), Decimal("16"))

    def test_received_transfer_can_only_be_paid_once(self):
        operation = receive_transfer(agent=self.agent, account_id=self.account.pk, amount=Decimal("100"), tariff_schedule=self.schedule)
        pay_received_transfer(operation_id=operation.pk, paid_by=self.agent)
        operation.refresh_from_db()
        self.account.refresh_from_db()
        self.assertEqual(operation.status, OperationStatus.COMPLETED)
        self.assertEqual(self.account.balance, Decimal("908.00"))
        with self.assertRaises(ValidationError):
            pay_received_transfer(operation_id=operation.pk, paid_by=self.agent)

    def test_expense_requires_accountant_and_admin(self):
        expense = Expense.objects.create(label="Loyer", amount=Decimal("100"), currency=self.usd, incurred_on=date.today(), created_by=self.admin)
        decide_expense(expense_id=expense.pk, actor=self.admin, decision=ApprovalDecision.APPROVED)
        expense.refresh_from_db()
        self.assertEqual(expense.status, ExpenseStatus.APPROVED)
        with self.assertRaises(ValidationError):
            decide_expense(expense_id=expense.pk, actor=self.admin, decision=ApprovalDecision.APPROVED)

    def test_unauthorized_role_cannot_approve_expense(self):
        expense = Expense.objects.create(label="Internet", amount=Decimal("50"), currency=self.usd, incurred_on=date.today(), created_by=self.admin)
        with self.assertRaises(PermissionDenied):
            decide_expense(expense_id=expense.pk, actor=self.partner, decision=ApprovalDecision.APPROVED)

    def test_profit_split_reconciles_to_net(self):
        operation = receive_transfer(agent=self.agent, account_id=self.account.pk, amount=Decimal("10"), tariff_schedule=self.schedule)
        pay_received_transfer(operation_id=operation.pk, paid_by=self.agent)
        expense = Expense.objects.create(label="Charge", amount=Decimal("1"), currency=self.usd, incurred_on=date.today(), created_by=self.admin)
        decide_expense(expense_id=expense.pk, actor=self.admin, decision=ApprovalDecision.APPROVED)
        period = ProfitPeriod.objects.create(start_date=date.today(), end_date=date.today(), currency=self.usd)
        calculate_profit_period(period_id=period.pk, actor=self.admin)
        period.refresh_from_db()
        self.assertEqual(period.gross_fees, Decimal("5.00"))
        self.assertEqual(period.deductible_expenses, Decimal("1.00"))
        self.assertEqual(period.net_profit, Decimal("4.00"))
        self.assertEqual(sum(period.allocations.values_list("amount", flat=True)), period.net_profit)
        self.assertEqual(list(period.allocations.order_by("bucket").values_list("percentage", flat=True)), [Decimal("60.00"), Decimal("20.00"), Decimal("20.00")])

    def test_sent_transfer_credits_amount_plus_fee(self):
        create_sent_transfer(agent=self.agent, account_id=self.account.pk, amount=Decimal("40.00"), tariff_schedule=self.schedule)
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("1045.00"))

    def test_foreign_currency_selects_tier_after_usd_conversion(self):
        eur = Currency.objects.create(code="EUR")
        ExchangeRate.objects.create(currency=eur, rate_to_usd=Decimal("0.500000"), effective_at=timezone.now(), created_by=self.admin)
        global_cash = GlobalCashAccount.objects.create(administrator=self.admin, currency=eur)
        adjust_global_cash(global_account_id=global_cash.pk, direction=MovementDirection.IN, amount=Decimal("10000"), adjusted_by=self.admin, note="Capital EUR")
        account = CashAccount.objects.create(agent=self.agent, currency=eur, global_account=global_cash, balance=Decimal("0"), is_active=True, allocated_by=self.admin)
        operation = create_sent_transfer(agent=self.agent, account_id=account.pk, amount=Decimal("80.00"), tariff_schedule=self.schedule)
        account.refresh_from_db()
        self.assertEqual(operation.amount_usd, Decimal("40.00"))
        self.assertEqual(operation.fee_usd, Decimal("5.00"))
        self.assertEqual(operation.fee, Decimal("10.00"))
        self.assertEqual(account.balance, Decimal("90.00"))

    def test_over_5000_requires_admin_fee_and_justification(self):
        with self.assertRaises(ValidationError):
            create_sent_transfer(agent=self.agent, account_id=self.account.pk, amount=Decimal("5000.01"), tariff_schedule=self.schedule, manual_fee=Decimal("190"), fee_justification="Cas exceptionnel")
        operation = create_sent_transfer(agent=self.admin, account_id=self.account.pk, amount=Decimal("5000.01"), tariff_schedule=self.schedule, manual_fee=Decimal("190"), fee_justification="Tarification validée")
        self.assertEqual(operation.fee_usd, Decimal("190.00"))

    def test_closure_variance_requires_reason_and_carries_counted_cash(self):
        with self.assertRaises(ValidationError):
            close_cash_day(account_id=self.account.pk, business_date=date.today(), declared_cash=Decimal("990"), closed_by=self.agent)
        closure = close_cash_day(account_id=self.account.pk, business_date=date.today(), declared_cash=Decimal("990"), closed_by=self.agent, justification="Écart de comptage vérifié")
        self.account.refresh_from_db()
        self.assertEqual(closure.status, ClosureStatus.CLOSED)
        self.assertEqual(closure.variance, Decimal("-10.00"))
        self.assertEqual(self.account.balance, Decimal("990.00"))
        tomorrow = DailyClosure.objects.get(account=self.account, business_date=date.today() + timedelta(days=1))
        self.assertEqual(tomorrow.opening_balance, Decimal("990.00"))

    def test_pdf_xlsx_csv_exports_share_the_same_snapshot(self):
        create_sent_transfer(agent=self.agent, account_id=self.account.pk, amount=Decimal("40.00"), tariff_schedule=self.schedule)
        exports = [generate_operation_report(user=self.admin, start_date=date.today(), end_date=date.today(), format=kind) for kind in ("PDF", "XLSX", "CSV")]
        self.assertTrue(all(export.status == "READY" and export.file for export in exports))
        self.assertEqual(exports[0].snapshot, exports[1].snapshot)
        self.assertEqual(exports[1].snapshot, exports[2].snapshot)
        self.assertEqual(exports[0].snapshot["totals"], {"count": 1, "by_currency": {"USD": {"amount": "40.00", "fees": "5.00"}}})

    def test_cancellation_reverses_exact_original_cash_impact(self):
        sent = create_sent_transfer(agent=self.agent, account_id=self.account.pk, amount=Decimal("40"), tariff_schedule=self.schedule)
        cancel_operation(operation_id=sent.pk, cancelled_by=self.agent, reason="Erreur de saisie")
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("1000.00"))
        withdrawal = create_withdrawal(agent=self.agent, account_id=self.account.pk, amount=Decimal("40"), tariff_schedule=self.schedule)
        cancel_operation(operation_id=withdrawal.pk, cancelled_by=self.agent, reason="Client absent")
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("1000.00"))

    def test_revision_creates_balancing_adjustment_and_blocks_fields(self):
        operation = create_sent_transfer(agent=self.agent, account_id=self.account.pk, amount=Decimal("40"), tariff_schedule=self.schedule)
        revise_operation(operation_id=operation.pk, changes={"amount": Decimal("30")}, reason="Montant corrigé", revised_by=self.agent)
        self.account.refresh_from_db()
        operation.refresh_from_db()
        self.assertEqual(operation.amount, Decimal("30.00"))
        self.assertEqual(self.account.balance, Decimal("1035.00"))
        self.assertEqual(operation.movements.filter(movement_type="ADJUSTMENT").count(), 1)
        with self.assertRaises(ValidationError):
            revise_operation(operation_id=operation.pk, changes={"status": "CANCELLED"}, reason="Interdit", revised_by=self.agent)

    def test_handover_permissions_and_separation_of_duties(self):
        reserve_before = self.global_cash.balance
        handover = handover_cash(account_id=self.account.pk, amount=Decimal("100"), requested_by=self.agent)
        with self.assertRaises(PermissionDenied):
            confirm_handover(handover_id=handover.pk, confirmed_by=self.agent)
        confirm_handover(handover_id=handover.pk, confirmed_by=self.admin)
        self.account.refresh_from_db()
        self.global_cash.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("900.00"))
        self.assertEqual(self.global_cash.balance, reserve_before + Decimal("100.00"))

    def test_future_exchange_rate_is_not_current(self):
        eur = Currency.objects.create(code="EUR")
        ExchangeRate.objects.create(currency=eur, rate_to_usd=Decimal("0.5"), effective_at=timezone.now() + timedelta(days=1), created_by=self.admin)
        self.assertIsNone(eur.current_rate())
        past = ExchangeRate.objects.create(currency=eur, rate_to_usd=Decimal("0.6"), effective_at=timezone.now() - timedelta(days=1), created_by=self.admin)
        self.assertEqual(eur.current_rate(), past)

    def test_overlapping_tariff_is_rejected(self):
        with self.assertRaises(ValidationError):
            TariffTier.objects.create(schedule=self.schedule, min_amount=Decimal("39"), max_amount=Decimal("41"), fixed_fee=Decimal("7"))

    def test_financial_ledgers_are_immutable_through_model_api(self):
        operation = create_sent_transfer(agent=self.agent, account_id=self.account.pk, amount=Decimal("40"), tariff_schedule=self.schedule)
        movement = operation.movements.get(movement_type="OPERATION")
        movement.note = "Altération"
        with self.assertRaises(ValidationError):
            movement.save()
        with self.assertRaises(ValidationError):
            movement.delete()
        event = AuditEvent.objects.filter(object_id=str(operation.pk)).first()
        event.action = "ALTERED"
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            CashMovement.objects.filter(pk=movement.pk).update(note="Altération en masse")
        with self.assertRaises(ValidationError):
            AuditEvent.objects.filter(pk=event.pk).delete()

    def test_global_cash_changes_require_an_immutable_ledger_entry(self):
        self.global_cash.balance += Decimal("1.00")
        with self.assertRaises(ValidationError):
            self.global_cash.save(update_fields=["balance"])
        self.global_cash.refresh_from_db()
        before = self.global_cash.balance
        movement = adjust_global_cash(
            global_account_id=self.global_cash.pk, direction=MovementDirection.IN,
            amount=Decimal("250.00"), adjusted_by=self.admin, note="Apport documenté",
        )
        self.global_cash.refresh_from_db()
        self.assertEqual(self.global_cash.balance, before + Decimal("250.00"))
        self.assertEqual(movement.balance_after, self.global_cash.balance)
        with self.assertRaises(ValidationError):
            GlobalCashMovement.objects.filter(pk=movement.pk).delete()

    def test_operation_creation_is_idempotent(self):
        first = create_sent_transfer(agent=self.agent, account_id=self.account.pk, amount=Decimal("40"), tariff_schedule=self.schedule, idempotency_key="form-unique-001")
        second = create_sent_transfer(agent=self.agent, account_id=self.account.pk, amount=Decimal("40"), tariff_schedule=self.schedule, idempotency_key="form-unique-001")
        self.account.refresh_from_db()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(self.account.balance, Decimal("1045.00"))

    def test_operation_rejects_oversized_idempotency_key(self):
        with self.assertRaises(ValidationError):
            create_sent_transfer(agent=self.agent, account_id=self.account.pk, amount=Decimal("40"), tariff_schedule=self.schedule, idempotency_key="k" * 65)

    def test_operation_stores_service_and_customer_details(self):
        operation = create_sent_transfer(
            agent=self.agent, account_id=self.account.pk, amount=Decimal("40"), tariff_schedule=self.schedule,
            service="VODACOM_MPESA", customer_identifier="+243810000000", customer_name="Jean Client",
        )
        self.assertEqual(operation.service, "VODACOM_MPESA")
        self.assertEqual(operation.customer_identifier, "+243810000000")
        self.assertEqual(operation.customer_name, "Jean Client")

    def test_admin_must_allocate_cash_before_agent_can_use_it(self):
        eur = Currency.objects.create(code="EUR")
        ExchangeRate.objects.create(currency=eur, rate_to_usd=Decimal("1"), effective_at=timezone.now(), created_by=self.admin)
        global_cash = GlobalCashAccount.objects.create(administrator=self.admin, currency=eur)
        adjust_global_cash(global_account_id=global_cash.pk, direction=MovementDirection.IN, amount=Decimal("1000"), adjusted_by=self.admin, note="Capital EUR")
        account = CashAccount.objects.create(agent=self.agent, currency=eur, global_account=global_cash)
        with self.assertRaises(ValidationError):
            create_sent_transfer(agent=self.agent, account_id=account.pk, amount=Decimal("20"), tariff_schedule=self.schedule)
        allocate_cash(account_id=account.pk, amount=Decimal("100"), allocated_by=self.admin, note="Fonds initiaux")
        create_withdrawal(agent=self.agent, account_id=account.pk, amount=Decimal("20"), tariff_schedule=self.schedule)
        account.refresh_from_db()
        self.assertTrue(account.is_active)
        self.assertEqual(account.balance, Decimal("85.00"))

    def test_admin_can_generate_consolidated_monthly_report(self):
        create_sent_transfer(agent=self.agent, account_id=self.account.pk, amount=Decimal("40"), tariff_schedule=self.schedule,
                             service="AIRTEL_MONEY", customer_identifier="+243810000000", customer_name="Client")
        export = generate_monthly_report(user=self.admin, year=date.today().year, month=date.today().month, format="XLSX")
        self.assertEqual(export.kind, "MONTHLY_FINANCIAL")
        self.assertEqual(len(export.snapshot["operations"]), 1)
        self.assertIn("salaries", export.snapshot)

    def test_monthly_expense_total_includes_approved_salaries(self):
        Expense.objects.create(
            label="Salaire agent", category="SALARY", agent=self.agent,
            amount=Decimal("125.00"), currency=self.usd, incurred_on=date.today(),
            created_by=self.admin, status=ExpenseStatus.APPROVED,
        )
        snapshot = monthly_financial_snapshot(
            user=self.admin, year=date.today().year, month=date.today().month,
        )
        self.assertEqual(snapshot["totals"]["USD"]["expenses"], "125.00")
