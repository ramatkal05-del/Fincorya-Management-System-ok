from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import date, datetime
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.cash.models import CashAccount, GlobalCashAccount
from apps.cash.services import adjust_global_cash, allocate_cash
from apps.expenses.models import Expense
from apps.expenses.services import decide_expense
from apps.finance.closing import (close_period, period_report, record_count,
    propose_distribution, approve_distributions, pay_distribution)
from apps.finance.events import counterpart, pay_expense, pay_partner
from apps.finance.imports import stage_import, review_import
from apps.finance.locking import save_internal
from apps.finance.models import (AccountType, EconomicRule, FinancialAccount,
    FinancialPeriod, ImportRow, JournalBatch, LedgerMutex)
from apps.finance.services import (ledger_balance, migrate_legacy_opening_balances,
    prepare_batch, post_batch, reconcile_account)
from apps.operations.services import create_sent_transfer, cancel_operation
from apps.pricing.models import Currency, ExchangeRate, TariffSchedule, TariffTier
from apps.profits.models import Distribution, ProfitPeriod
from apps.stakeholders.models import Stakeholder

ZONE = ZoneInfo('Europe/Istanbul')
JULY = datetime(2026, 7, 15, 12, tzinfo=ZONE)
AUGUST = datetime(2026, 8, 3, 12, tzinfo=ZONE)


class FinanceScenario:
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(email='finance-admin@test.local', role=Role.ADMIN)
        self.manager = User.objects.create_user(email='finance-manager@test.local', role=Role.FINANCE_MANAGER)
        self.agent = User.objects.create_user(email='finance-agent@test.local', role=Role.AGENT)
        self.usd = Currency.objects.create(code='USD')
        self.global_cash = GlobalCashAccount.objects.create(administrator=self.admin, currency=self.usd)
        self.legacy = CashAccount.objects.create(agent=self.agent, currency=self.usd, global_account=self.global_cash)
        with override_settings(FINANCE_LEDGER_ENABLED=False), patch('django.utils.timezone.now', return_value=datetime(2026, 6, 1, 12, tzinfo=ZONE)):
            adjust_global_cash(global_account_id=self.global_cash.pk, direction='IN', amount=Decimal('10000'), adjusted_by=self.admin, note='Test capital')
            allocate_cash(account_id=self.legacy.pk, amount=Decimal('1000'), allocated_by=self.admin, note='Test allocation')
        cutover = datetime(2026, 6, 2, 12, tzinfo=ZONE)
        preview = migrate_legacy_opening_balances(actor=self.admin, cutover_at=cutover)
        migrate_legacy_opening_balances(actor=self.admin, cutover_at=cutover, apply=True, expected_hash=preview['preview_hash'])
        self.cash = FinancialAccount.objects.get(legacy_cash_account=self.legacy)
        self.partner = Stakeholder.objects.create(name='Partner by identifier', type='PARTNER')
        EconomicRule.objects.create(stakeholder=self.partner, kind='COMMISSION', value=60, effective_from=date(2026, 7, 1), created_by=self.admin)
        ExchangeRate.objects.create(currency=self.usd, rate_to_usd=1, effective_at=datetime(2026, 6, 1, tzinfo=ZONE), created_by=self.admin)
        self.tariff = TariffSchedule.objects.create(name='Test', currency=self.usd, is_published=True)
        TariffTier.objects.create(schedule=self.tariff, min_amount=Decimal('.01'), max_amount=5000, fixed_fee=100)

    def operation(self, key='op', partner=None, when=JULY):
        with patch('django.utils.timezone.now', return_value=when):
            return create_sent_transfer(agent=self.agent, account_id=self.legacy.pk, amount=Decimal('1000'),
                tariff_schedule=self.tariff, stakeholder=partner, commission_owner_confirmed=True, idempotency_key=key)

    def period(self, kind='MONTH', start=date(2026, 7, 1), end=date(2026, 7, 31)):
        return FinancialPeriod.objects.create(period_type=kind, start_date=start, end_date=end)

    def count_all(self, period):
        for row in period_report(period)['accounts']:
            if row['account'].account_type in {'AGENT_CASH', 'GLOBAL_CASH'}:
                record_count(period_id=period.pk, account_id=row['account'].pk,
                    declared=row['closing'], justification='', actor=self.admin)

    def expense(self, amount='50'):
        row = Expense.objects.create(label='Test expense', amount=Decimal(amount), currency=self.usd,
            incurred_on=date(2026, 7, 10), created_by=self.admin)
        decide_expense(expense_id=row.pk, actor=self.admin, decision='APPROVED')
        return row


@override_settings(FINANCE_LEDGER_ENABLED=True, LOCAL_AUTH_BYPASS=False, MFA_ENABLED=False, SECURE_SSL_REDIRECT=False)
class FinanceWorkflowTests(FinanceScenario, TestCase):
    def test_global_expense_payment_is_not_recorded_as_capital_withdrawal(self):
        expense = self.expense()
        cash = FinancialAccount.objects.get(legacy_global_account=self.global_cash)
        before = JournalBatch.objects.count()
        with patch('django.utils.timezone.now', return_value=AUGUST):
            pay_expense(expense_id=expense.pk, account_id=cash.pk, amount=Decimal('50'),
                actor=self.admin, idempotency_key='global-expense')
        self.assertEqual(JournalBatch.objects.count(), before + 1)
        self.assertEqual(ledger_balance(cash), Decimal('8950'))
        self.global_cash.refresh_from_db()
        self.assertEqual(self.global_cash.balance, ledger_balance(cash))

    def test_capital_allocation_and_handover_keep_both_cash_projections_equal(self):
        from apps.cash.services import handover_cash, confirm_handover
        with patch('django.utils.timezone.now', return_value=JULY):
            adjust_global_cash(global_account_id=self.global_cash.pk, direction='IN',
                amount=Decimal('100'), adjusted_by=self.admin, note='Additional capital')
            allocate_cash(account_id=self.legacy.pk, amount=Decimal('200'), allocated_by=self.admin)
            handover = handover_cash(account_id=self.legacy.pk, amount=Decimal('50'), requested_by=self.agent)
            confirm_handover(handover_id=handover.pk, confirmed_by=self.admin)
        self.legacy.refresh_from_db()
        self.global_cash.refresh_from_db()
        self.assertEqual(self.legacy.balance, Decimal('1150'))
        self.assertEqual(self.global_cash.balance, Decimal('8950'))
        self.assertEqual(ledger_balance(self.cash), self.legacy.balance)
        self.assertEqual(ledger_balance(FinancialAccount.objects.get(legacy_global_account=self.global_cash)), self.global_cash.balance)

    def test_partner_commission_payment_and_cancellation_use_the_same_ledger(self):
        op = self.operation(partner=self.partner)
        self.assertEqual(self.operation(partner=self.partner).pk, op.pk)
        payable = counterpart(self.usd, AccountType.PARTNER_PAYABLE, party=self.partner)
        income = counterpart(self.usd, AccountType.COMMISSION)
        self.assertEqual(ledger_balance(payable), Decimal('60'))
        self.assertEqual(ledger_balance(income), Decimal('40'))
        self.assertEqual(JournalBatch.objects.filter(event_type='OPERATION').count(), 1)
        with patch('django.utils.timezone.now', return_value=AUGUST):
            payment = pay_partner(party_id=self.partner.pk, account_id=self.cash.pk, amount=Decimal('20'), actor=self.admin, idempotency_key='partner-payment')
            self.assertEqual(pay_partner(party_id=self.partner.pk, account_id=self.cash.pk, amount=Decimal('20'), actor=self.admin, idempotency_key='partner-payment').pk, payment.pk)
            with self.assertRaises(ValidationError):
                pay_partner(party_id=self.partner.pk, account_id=self.cash.pk, amount=Decimal('41'), actor=self.admin, idempotency_key='overpay')
        self.assertEqual(ledger_balance(payable), Decimal('40'))
        self.legacy.refresh_from_db()
        self.assertEqual(self.legacy.balance, ledger_balance(self.cash))
        self.client.force_login(self.admin)
        response = self.client.get(reverse('finance:partner', args=[self.partner.pk]))
        self.assertContains(response, str(op.reference))
        self.assertContains(response, '20,00')

    def test_cancellation_reverses_partner_commission_without_repricing_history(self):
        op = self.operation(partner=self.partner)
        EconomicRule.objects.create(stakeholder=self.partner, kind='COMMISSION', value=70, effective_from=date(2026, 8, 1), created_by=self.admin)
        with patch('django.utils.timezone.now', return_value=AUGUST):
            cancel_operation(operation_id=op.pk, cancelled_by=self.admin, reason='Test cancellation')
        self.assertEqual(ledger_balance(counterpart(self.usd, AccountType.PARTNER_PAYABLE, party=self.partner)), 0)
        self.assertEqual(ledger_balance(self.cash), Decimal('1000'))

    def test_expense_payment_does_not_reduce_profit_twice(self):
        expense = self.expense()
        period = self.period()
        before = period_report(period)['totals']['USD']
        self.assertEqual(before['expenses'], Decimal('50'))
        with patch('django.utils.timezone.now', return_value=JULY):
            payment = pay_expense(expense_id=expense.pk, account_id=self.cash.pk, amount=Decimal('50'), actor=self.admin, idempotency_key='expense-payment')
            self.assertEqual(pay_expense(expense_id=expense.pk, account_id=self.cash.pk, amount=Decimal('50'), actor=self.admin, idempotency_key='expense-payment').pk, payment.pk)
        after = period_report(period)['totals']['USD']
        self.assertEqual(after['expenses'], before['expenses'])
        self.assertEqual(after['cash'], before['cash'] - 50)
        self.assertEqual(ledger_balance(counterpart(self.usd, AccountType.EXPENSE_PAYABLE)), 0)

    def test_monthly_salary_can_follow_week_closure_but_cash_remains_locked(self):
        party = Stakeholder.objects.create(name='Monthly worker', type='STAFF')
        EconomicRule.objects.create(stakeholder=party, kind='REMUNERATION', value=50, currency=self.usd,
            effective_from=date(2026, 7, 1), created_by=self.admin)
        week = self.period('WEEK', date(2026, 7, 27), date(2026, 8, 2))
        self.count_all(week)
        with patch('django.utils.timezone.now', return_value=AUGUST):
            close_period(period_id=week.pk, actor=self.admin)
            month = self.period()
            self.count_all(month)
            close_period(period_id=month.pk, actor=self.admin)
            close_period(period_id=month.pk, actor=self.admin)
        self.assertEqual(Expense.objects.filter(accrual_key__startswith='remuneration-').count(), 1)
        profit = ProfitPeriod.objects.get(finance_period=month)
        self.assertEqual(profit.deductible_expenses, Decimal('50'))
        with self.assertRaises(ValidationError):
            self.operation('locked', when=datetime(2026, 7, 31, 12, tzinfo=ZONE))
        salary = Expense.objects.get(accrual_key__startswith='remuneration-')
        with patch('django.utils.timezone.now', return_value=AUGUST):
            pay_expense(expense_id=salary.pk, account_id=self.cash.pk, amount=Decimal('50'), actor=self.admin, idempotency_key='salary-later')
        self.assertEqual(period_report(month)['totals']['USD']['expenses'], Decimal('50'))

    def test_proposal_approval_and_payment_are_separate_and_authorized(self):
        self.operation()
        for index in range(4):
            party = Stakeholder.objects.create(name=f'Shareholder {index}', type='SHAREHOLDER')
            EconomicRule.objects.create(stakeholder=party, kind='DIVIDEND', value=25, effective_from=date(2026, 7, 1), created_by=self.admin)
        month = self.period()
        self.count_all(month)
        with patch('django.utils.timezone.now', return_value=AUGUST):
            close_period(period_id=month.pk, actor=self.admin)
            profit = ProfitPeriod.objects.get(finance_period=month)
            with self.assertRaises(ValidationError):
                approve_distributions(profit_id=profit.pk, amount=Decimal('80'), actor=self.admin)
            propose_distribution(profit_id=profit.pk, amount=Decimal('80'), actor=self.manager)
            self.assertFalse(Distribution.objects.exists())
            with self.assertRaises(PermissionDenied):
                approve_distributions(profit_id=profit.pk, amount=Decimal('80'), actor=self.manager)
            approve_distributions(profit_id=profit.pk, amount=Decimal('80'), actor=self.admin)
            approve_distributions(profit_id=profit.pk, amount=Decimal('80'), actor=self.admin)
            self.assertEqual(list(Distribution.objects.values_list('amount', flat=True)), [Decimal('20')] * 4)
            row = Distribution.objects.first()
            cash_before = ledger_balance(self.cash)
            pay_distribution(distribution_id=row.pk, account_id=self.cash.pk, actor=self.admin)
            pay_distribution(distribution_id=row.pk, account_id=self.cash.pk, actor=self.admin)
            self.assertEqual(ledger_balance(self.cash), cash_before - 20)
            reinvested = Distribution.objects.exclude(pk=row.pk).first()
            capital = counterpart(self.usd, AccountType.CAPITAL, party=reinvested.stakeholder)
            capital_before = ledger_balance(capital)
            pay_distribution(distribution_id=reinvested.pk, destination='REINVEST', actor=self.admin)
            reinvested.refresh_from_db()
            self.assertEqual(reinvested.status, 'REINVESTED')
            self.assertEqual(ledger_balance(self.cash), cash_before - 20)
            self.assertEqual(ledger_balance(capital), capital_before + 20)
            self.assertEqual(ledger_balance(counterpart(self.usd, AccountType.DISTRIBUTION_PAYABLE, party=reinvested.stakeholder)), 0)
            with self.assertRaises(ValidationError):
                pay_distribution(distribution_id=Distribution.objects.filter(status='DUE').first().pk, destination='PAYOUT', actor=self.admin)
        profit.refresh_from_db()
        self.assertEqual(profit.net_profit, Decimal('100'))

    def test_week_across_months_does_not_move_august_income_into_july(self):
        self.operation('july', when=datetime(2026, 7, 31, 23, tzinfo=ZONE))
        self.operation('august', when=datetime(2026, 8, 1, 1, tzinfo=ZONE))
        month = self.period()
        week = self.period('WEEK', date(2026, 7, 27), date(2026, 8, 2))
        self.assertEqual(period_report(month)['totals']['USD']['income'], 100)
        self.assertEqual(period_report(week)['totals']['USD']['income'], 200)

    def test_import_review_never_posts_or_invents_accounts(self):
        rows = [{'source_key':'uncertain', 'kind':'account', 'service':'M-Pesa', 'balance':None, 'synthetic_adjustment':True}]
        before = JournalBatch.objects.count(), FinancialAccount.objects.count()
        preview = stage_import(actor=self.admin, rows=rows)
        self.assertTrue(preview[0]['anomalies'])
        self.assertFalse(ImportRow.objects.exists())
        stage_import(actor=self.admin, rows=rows, apply=True)
        stage_import(actor=self.admin, rows=rows, apply=True)
        row = ImportRow.objects.get()
        review_import(row_id=row.pk, actor=self.admin, resolution='Excluded: artificial source, evidence reference TEST-1')
        self.assertEqual(before, (JournalBatch.objects.count(), FinancialAccount.objects.count()))
        with self.assertRaises(ValidationError):
            review_import(row_id=row.pk, actor=self.admin, resolution='Overwrite reviewed evidence is forbidden')

    def test_alias_identity_is_shared_across_all_creation_paths(self):
        first = Stakeholder.objects.create(name='Jenovic Mpoto', type='STAFF')
        self.assertEqual(first.name, 'Mpoto Jenovic')
        with self.assertRaises(ValidationError):
            Stakeholder.objects.create(name='Mpoto Jenovic', type='STAFF')

    def test_finance_screens_and_role_boundaries(self):
        self.client.force_login(self.manager)
        for section in ['parties','rules','services','journal','periods','imports','reconciliation','expenses','profits','distributions']:
            self.assertEqual(self.client.get(reverse('finance:workspace', args=[section])).status_code, 200)
        self.assertEqual(self.client.get(reverse('finance:batch_create')).status_code, 200)
        self.assertEqual(self.client.get(reverse('finance:account_create')).status_code, 403)
        self.client.force_login(self.agent)
        self.assertEqual(self.client.get(reverse('finance:overview')).status_code, 403)


def in_thread(function, **kwargs):
    try:
        return function(**kwargs)
    finally:
        connections.close_all()


@skipUnless(connection.vendor == 'postgresql', 'PostgreSQL required for row locks')
@override_settings(FINANCE_LEDGER_ENABLED=True)
class FinanceConcurrencyTests(FinanceScenario, TransactionTestCase):
    def test_concurrent_expense_payments_cannot_overpay(self):
        expense = self.expense('50')
        def pay(key):
            try:
                return in_thread(pay_expense, expense_id=expense.pk, account_id=self.cash.pk,
                    amount=Decimal('50'), actor=self.admin, idempotency_key=key).pk
            except ValidationError:
                return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(pay, ['one', 'two']))
        self.assertEqual(results.count('rejected'), 1)
        self.assertEqual(ledger_balance(self.cash), Decimal('950'))
        self.cash.refresh_from_db()
        self.assertEqual(reconcile_account(self.cash)['difference'], 0)

    def test_period_lock_serializes_with_a_waiting_post(self):
        period = self.period()
        capital = counterpart(self.usd, AccountType.CAPITAL)
        batch = prepare_batch(actor=self.admin, idempotency_key='waiting', event_type='MANUAL', effective_at=JULY,
            description='Waiting post', lines=[{'account':self.cash,'currency':self.usd,'side':'DEBIT','amount':Decimal('10')},
                {'account':capital,'currency':self.usd,'side':'CREDIT','amount':Decimal('10')}])
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                LedgerMutex.objects.select_for_update().get(pk=1)
                future = pool.submit(in_thread, post_batch, batch_id=batch.pk, actor=self.admin)
                with self.assertRaises(TimeoutError):
                    future.result(timeout=.2)
                period.status = 'LOCKED'
                save_internal(period)
            with self.assertRaises(ValidationError):
                future.result(timeout=10)
        self.assertEqual(ledger_balance(self.cash), Decimal('1000'))
