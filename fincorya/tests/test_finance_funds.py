"""Acceptance tests for fund origin, allocation, partner rules, result and distribution."""
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings

from apps.accounts.models import Role, User
from apps.expenses.models import Expense
from apps.expenses.services import decide_expense
from apps.finance.closing import approve_distributions, close_period, propose_distribution
from apps.finance.events import counterpart
from apps.finance.funds import (cancel_transfer, confirm_transfer, contributed_total, decide_request, execute_request,
                                guarantee_balance, in_transit, initiate_transfer, record_contribution, submit_request)
from apps.finance.models import AccountType, DistributionPolicy, FinancialAccount, JournalBatch, StakeholderRequest
from apps.finance.results import split_amount
from apps.finance.services import ledger_balance
from apps.profits.models import Distribution, ProfitPeriod
from apps.stakeholders.models import Stakeholder
from tests.test_finance_workflows import AUGUST, JULY, ZONE, FinanceScenario

SEPTEMBER = datetime(2026, 9, 2, 12, tzinfo=ZONE)


@override_settings(FINANCE_LEDGER_ENABLED=True, LOCAL_AUTH_BYPASS=False, MFA_ENABLED=False, SECURE_SSL_REDIRECT=False)
class FundOriginTests(FinanceScenario, TestCase):
    def setUp(self):
        super().setUp()
        self.shareholder = Stakeholder.objects.create(name='Shareholder A', type='SHAREHOLDER')
        self.investor = Stakeholder.objects.create(name='Investor A', type='INVESTOR')
        self.capital = counterpart(self.usd, AccountType.CAPITAL, party=self.shareholder)

    def contribute(self, key='capital-1', amount='500', origin='SHAREHOLDER', party=None, account=None):
        with patch('django.utils.timezone.now', return_value=JULY):
            return record_contribution(actor=self.admin, client_key=key, origin=origin, stakeholder_id=(party or self.shareholder).pk,
                amount=Decimal(amount), currency=self.usd, received_on=date(2026, 7, 15), account_id=(account or self.service_account).pk)

    def test_contribution_is_recorded_once_and_replay_does_not_duplicate(self):
        first = self.contribute()
        again = self.contribute()
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(JournalBatch.objects.filter(event_type='CONTRIBUTION', source_id=str(first.pk)).count(), 1)
        self.assertEqual(ledger_balance(self.capital), Decimal('500'))
        self.assertEqual(ledger_balance(self.service_account), Decimal('2500'))
        with self.assertRaises(ValidationError):
            self.contribute(amount='600')

    def test_contribution_origin_must_match_party_type(self):
        with self.assertRaises(ValidationError):
            self.contribute(key='wrong', origin='INVESTOR')

    def test_legacy_opening_balances_are_not_contributions_again(self):
        # Opening balances came from the reconciled cutover; recording them as contributions is a distinct, explicit act.
        self.assertEqual(ledger_balance(FinancialAccount.objects.get(code='OPENING-EQUITY-USD')), Decimal('10000'))
        self.assertEqual(contributed_total(self.shareholder, self.usd), Decimal('0'))
        self.contribute()
        self.assertEqual(contributed_total(self.shareholder, self.usd), Decimal('500'))
        self.assertEqual(ledger_balance(FinancialAccount.objects.get(code='OPENING-EQUITY-USD')), Decimal('10000'))

    def test_allocation_moves_funds_without_changing_total(self):
        self.contribute()
        treasury_before = ledger_balance(self.service_account) + ledger_balance(self.cash)
        with patch('django.utils.timezone.now', return_value=JULY):
            transfer = initiate_transfer(actor=self.admin, client_key='t-1', source_id=self.service_account.pk, destination_id=self.cash.pk, amount=Decimal('300'))
            self.assertEqual(initiate_transfer(actor=self.admin, client_key='t-1', source_id=self.service_account.pk, destination_id=self.cash.pk, amount=Decimal('300')).pk, transfer.pk)
            self.assertEqual(in_transit(self.usd), Decimal('300'))
            self.assertEqual(ledger_balance(self.service_account) + ledger_balance(self.cash) + in_transit(self.usd), treasury_before)
            confirm_transfer(actor=self.admin, transfer_id=transfer.pk)
            confirm_transfer(actor=self.admin, transfer_id=transfer.pk)
        self.assertEqual(in_transit(self.usd), Decimal('0'))
        self.assertEqual(ledger_balance(self.service_account) + ledger_balance(self.cash), treasury_before)
        self.legacy.refresh_from_db()
        self.assertEqual(self.legacy.balance, ledger_balance(self.cash))

    def test_transfer_fee_is_an_expense_and_cancellation_returns_transit_funds(self):
        with patch('django.utils.timezone.now', return_value=JULY):
            transfer = initiate_transfer(actor=self.admin, client_key='t-fee', source_id=self.service_account.pk, destination_id=self.cash.pk, amount=Decimal('100'), fee=Decimal('2'))
            self.assertEqual(ledger_balance(self.service_account), Decimal('1898'))
            self.assertEqual(ledger_balance(counterpart(self.usd, AccountType.EXPENSE, category='TRANSFER_FEE')), Decimal('2'))
            cancel_transfer(actor=self.admin, transfer_id=transfer.pk, reason='Provider failure')
        self.assertEqual(ledger_balance(self.service_account), Decimal('1998'))
        self.assertEqual(in_transit(self.usd), Decimal('0'))
        with self.assertRaises(ValidationError):
            initiate_transfer(actor=self.admin, client_key='too-much', source_id=self.service_account.pk, destination_id=self.cash.pk, amount=Decimal('5000'))

    def test_one_service_can_hold_several_accounts_and_cash_is_separated_by_currency(self):
        from apps.pricing.models import Currency
        eur = Currency.objects.create(code='EUR')
        second = FinancialAccount.objects.create(code='MPESA-USD-2', name='M-Pesa USD compte 2', account_type='MOBILE_MONEY', nature='ASSET', currency=self.usd)
        eur_box = FinancialAccount.objects.create(code='MPESA-EUR-1', name='M-Pesa EUR', account_type='MOBILE_MONEY', nature='ASSET', currency=eur)
        with patch('django.utils.timezone.now', return_value=JULY):
            initiate_transfer(actor=self.admin, client_key='split', source_id=self.service_account.pk, destination_id=second.pk, amount=Decimal('700'))
            with self.assertRaises(ValidationError):
                initiate_transfer(actor=self.admin, client_key='cross', source_id=self.service_account.pk, destination_id=eur_box.pk, amount=Decimal('10'))
        self.assertEqual(ledger_balance(self.service_account), Decimal('1300'))
        self.assertEqual(ledger_balance(eur_box), Decimal('0'))


@override_settings(FINANCE_LEDGER_ENABLED=True, LOCAL_AUTH_BYPASS=False, MFA_ENABLED=False, SECURE_SSL_REDIRECT=False)
class PartnerRuleTests(FinanceScenario, TestCase):
    def test_partner_commission_splits_60_40_and_supplier_fee_stays_a_fincorya_charge(self):
        from apps.operations.services import create_sent_transfer
        with patch('django.utils.timezone.now', return_value=JULY):
            create_sent_transfer(agent=self.agent, account_id=self.legacy.pk, amount=Decimal('1000'), tariff_schedule=self.tariff,
                stakeholder=self.partner, commission_owner_confirmed=True, idempotency_key='supplier', supplier_fee=Decimal('5'))
        payable = counterpart(self.usd, AccountType.PARTNER_PAYABLE, party=self.partner)
        self.assertEqual(ledger_balance(payable), Decimal('60'))
        self.assertEqual(ledger_balance(counterpart(self.usd, AccountType.COMMISSION)), Decimal('40'))
        self.assertEqual(ledger_balance(counterpart(self.usd, AccountType.EXPENSE, category='SUPPLIER_FEE')), Decimal('5'))
        self.assertEqual(ledger_balance(self.cash), Decimal('1000') + 1000 + 100 - 5)
        self.legacy.refresh_from_db()
        self.assertEqual(self.legacy.balance, ledger_balance(self.cash))

    def test_commission_conversion_increases_guarantee_without_new_cash(self):
        self.operation(partner=self.partner)
        treasury = ledger_balance(self.cash) + ledger_balance(self.service_account)
        request = submit_request(actor=self.admin, kind='CONVERT_COMMISSION', amount=Decimal('60'), currency=self.usd, stakeholder_id=self.partner.pk)
        self.assertEqual(guarantee_balance(self.partner, self.usd), Decimal('2000'))
        decide_request(actor=self.admin, request_id=request.pk, decision='APPROVED')
        execute_request(actor=self.admin, request_id=request.pk)
        execute_request(actor=self.admin, request_id=request.pk)
        self.assertEqual(guarantee_balance(self.partner, self.usd), Decimal('2060'))
        self.assertEqual(ledger_balance(counterpart(self.usd, AccountType.PARTNER_PAYABLE, party=self.partner)), Decimal('0'))
        self.assertEqual(ledger_balance(self.cash) + ledger_balance(self.service_account), treasury)
        with self.assertRaises(ValidationError):
            submit_request(actor=self.admin, kind='CONVERT_COMMISSION', amount=Decimal('1'), currency=self.usd, stakeholder_id=self.partner.pk)

    def test_partner_ceiling_is_checked_per_operation(self):
        from apps.operations.services import create_sent_transfer
        with patch('django.utils.timezone.now', return_value=JULY):
            with self.assertRaises(ValidationError):
                create_sent_transfer(agent=self.agent, account_id=self.legacy.pk, amount=Decimal('1600'), tariff_schedule=self.tariff,
                    stakeholder=self.partner, commission_owner_confirmed=True, idempotency_key='over-ceiling')
            create_sent_transfer(agent=self.agent, account_id=self.legacy.pk, amount=Decimal('1400'), tariff_schedule=self.tariff,
                stakeholder=self.partner, commission_owner_confirmed=True, idempotency_key='within')
        self.assertEqual(JournalBatch.objects.filter(event_type='OPERATION').count(), 1)
        Stakeholder.objects.create(name='Partner without guarantee', type='PARTNER')
        with self.assertRaises(ValidationError):
            self.operation('no-guarantee', partner=Stakeholder.objects.get(name='Partner without guarantee'))


@override_settings(FINANCE_LEDGER_ENABLED=True, LOCAL_AUTH_BYPASS=False, MFA_ENABLED=False, SECURE_SSL_REDIRECT=False)
class ResultAndDistributionTests(FinanceScenario, TestCase):
    def setUp(self):
        super().setUp()
        DistributionPolicy.objects.create(mode='EQUAL_SHARES', effective_from=date(2026, 7, 1), created_by=self.admin)
        self.shareholders = [Stakeholder.objects.create(name=f'Equal shareholder {i}', type='SHAREHOLDER') for i in range(4)]
        self.investor = Stakeholder.objects.create(name='Investor', type='INVESTOR')

    def charge(self, amount, category, agent=None, stakeholder=None):
        row = Expense.objects.create(label=f'{category} test', category=category, agent=agent, stakeholder=stakeholder, amount=Decimal(amount),
            currency=self.usd, incurred_on=date(2026, 7, 20), created_by=self.admin)
        decide_expense(expense_id=row.pk, actor=self.admin, decision='APPROVED')
        return row

    def test_brief_example_result_and_equal_shares(self):
        from apps.operations.services import create_sent_transfer
        for i in range(10):
            self.operation(f'own-{i}')
        for i in range(5):
            self.operation(f'partner-{i}', partner=self.partner)
        # Supplier fee: 20 USD on one own operation (fee unchanged, charge separate).
        with patch('django.utils.timezone.now', return_value=JULY):
            create_sent_transfer(agent=self.agent, account_id=self.legacy.pk, amount=Decimal('1000'), tariff_schedule=self.tariff,
                commission_owner_confirmed=True, idempotency_key='with-supplier-fee', manual_fee=Decimal('0'), supplier_fee=Decimal('20'))
        self.charge('100', 'SALARY', agent=self.agent)
        self.charge('50', 'GENERAL')
        self.charge('40', 'INVESTOR_RETURN', stakeholder=self.investor)
        month = self.period()
        self.count_all(month)
        with patch('django.utils.timezone.now', return_value=AUGUST):
            close_period(period_id=month.pk, actor=self.admin)
            profit = ProfitPeriod.objects.get(finance_period=month)
            result = profit.snapshot['result']
            self.assertEqual(result['own_commissions'], '1000.00')
            self.assertEqual(result['partner_gross'], '500.00')
            self.assertEqual(result['fincorya_share'], '200.00')
            self.assertEqual(result['salaries'], '100.00')
            self.assertEqual(result['other_expenses'], '50.00')
            self.assertEqual(result['supplier_fees'], '20.00')
            self.assertEqual(result['investor_returns'], '40.00')
            self.assertEqual(profit.net_profit, Decimal('990'))
            self.assertEqual(profit.distributable, Decimal('990'))
            self.assertEqual(ledger_balance(counterpart(self.usd, AccountType.PARTNER_PAYABLE, party=self.partner)), Decimal('300'))
            propose_distribution(profit_id=profit.pk, amount=Decimal('990'), actor=self.admin)
            approve_distributions(profit_id=profit.pk, amount=Decimal('990'), actor=self.admin)
        parts = list(Distribution.objects.order_by('stakeholder_id').values_list('amount', flat=True))
        self.assertEqual(parts, [Decimal('247.50')] * 4)
        self.assertEqual(profit.snapshot['distribution_plan']['mode'], 'EQUAL_SHARES')

    def test_expense_paid_in_period_is_not_deducted_twice(self):
        from apps.finance.events import pay_expense
        self.operation()
        expense = self.charge('30', 'GENERAL')
        with patch('django.utils.timezone.now', return_value=JULY):
            pay_expense(expense_id=expense.pk, account_id=self.cash.pk, amount=Decimal('30'), actor=self.admin, idempotency_key='paid-in-period')
        month = self.period()
        self.count_all(month)
        with patch('django.utils.timezone.now', return_value=AUGUST):
            close_period(period_id=month.pk, actor=self.admin)
        self.assertEqual(ProfitPeriod.objects.get(finance_period=month).net_profit, Decimal('70'))

    def test_loss_produces_no_distribution_and_is_carried_forward(self):
        self.charge('150', 'GENERAL')
        july = self.period()
        self.count_all(july)
        with patch('django.utils.timezone.now', return_value=AUGUST):
            close_period(period_id=july.pk, actor=self.admin)
            loss = ProfitPeriod.objects.get(finance_period=july)
            self.assertEqual(loss.distributable, Decimal('-150'))
            with self.assertRaises(ValidationError):
                propose_distribution(profit_id=loss.pk, amount=Decimal('1'), actor=self.admin)
        self.operation('aug-1', when=AUGUST)
        self.operation('aug-2', when=AUGUST)
        august = self.period(start=date(2026, 8, 1), end=date(2026, 8, 31))
        self.count_all(august)
        with patch('django.utils.timezone.now', return_value=SEPTEMBER):
            close_period(period_id=august.pk, actor=self.admin)
        profit = ProfitPeriod.objects.get(finance_period=august)
        self.assertEqual(profit.net_profit, Decimal('200'))
        self.assertEqual(profit.prior_losses, Decimal('150'))
        self.assertEqual(profit.distributable, Decimal('50'))

    def test_rounding_keeps_sum_equal_to_distributed_amount(self):
        weights = [{'stakeholder_id': i, 'weight': '1'} for i in range(3)]
        parts = split_amount(Decimal('100'), weights)
        self.assertEqual(sum(p for _, p in parts), Decimal('100'))
        self.assertEqual([p for _, p in parts], [Decimal('33.33'), Decimal('33.33'), Decimal('33.34')])
        parts = split_amount(Decimal('0.05'), [{'stakeholder_id': i, 'weight': '1'} for i in range(4)])
        self.assertEqual(sum(p for _, p in parts), Decimal('0.05'))

    def test_closed_period_stays_identical_after_later_rule_changes(self):
        self.operation()
        month = self.period()
        self.count_all(month)
        with patch('django.utils.timezone.now', return_value=AUGUST):
            close_period(period_id=month.pk, actor=self.admin)
        profit = ProfitPeriod.objects.get(finance_period=month)
        before = (profit.net_profit, profit.snapshot)
        DistributionPolicy.objects.create(mode='RULE_PERCENT', effective_from=date(2026, 8, 1), created_by=self.admin)
        from apps.pricing.models import ExchangeRate
        ExchangeRate.objects.create(currency=self.usd, rate_to_usd=Decimal('1.5'), effective_at=AUGUST, created_by=self.admin)
        profit.refresh_from_db()
        self.assertEqual((profit.net_profit, profit.snapshot), before)
        with self.assertRaises(ValidationError):
            self.operation('after-lock', when=datetime(2026, 7, 20, 12, tzinfo=ZONE))


@override_settings(FINANCE_LEDGER_ENABLED=True, LOCAL_AUTH_BYPASS=False, MFA_ENABLED=False, SECURE_SSL_REDIRECT=False)
class StakeholderRequestTests(FinanceScenario, TestCase):
    def setUp(self):
        super().setUp()
        DistributionPolicy.objects.create(mode='EQUAL_SHARES', effective_from=date(2026, 7, 1), created_by=self.admin)
        self.holder_user = User.objects.create_user(email='holder@test.local', role=Role.SHAREHOLDER)
        self.other_user = User.objects.create_user(email='other-holder@test.local', role=Role.SHAREHOLDER)
        self.holder = Stakeholder.objects.create(name='Holder', type='SHAREHOLDER', owner=self.holder_user)
        self.other = Stakeholder.objects.create(name='Other holder', type='SHAREHOLDER', owner=self.other_user)
        self.operation()
        self.operation('second')
        month = self.period()
        self.count_all(month)
        with patch('django.utils.timezone.now', return_value=AUGUST):
            close_period(period_id=month.pk, actor=self.admin)
            self.profit = ProfitPeriod.objects.get(finance_period=month)
            propose_distribution(profit_id=self.profit.pk, amount=Decimal('200'), actor=self.admin)
            approve_distributions(profit_id=self.profit.pk, amount=Decimal('200'), actor=self.admin)
        self.distribution = Distribution.objects.get(stakeholder=self.holder)

    def test_reinvestment_acts_only_after_validation_and_partial_cannot_exceed_available(self):
        capital = counterpart(self.usd, AccountType.CAPITAL, party=self.holder)
        request = submit_request(actor=self.holder_user, kind='REINVEST_PROFIT', amount=Decimal('60'), currency=self.usd, distribution_id=self.distribution.pk)
        self.assertEqual(ledger_balance(capital), Decimal('0'))
        with self.assertRaises(ValidationError):
            execute_request(actor=self.admin, request_id=request.pk)
        with self.assertRaises(ValidationError):
            submit_request(actor=self.holder_user, kind='REINVEST_PROFIT', amount=Decimal('50'), currency=self.usd, distribution_id=self.distribution.pk)
        with self.assertRaises(PermissionDenied):
            decide_request(actor=self.holder_user, request_id=request.pk, decision='APPROVED')
        decide_request(actor=self.admin, request_id=request.pk, decision='APPROVED')
        execute_request(actor=self.admin, request_id=request.pk)
        self.assertEqual(ledger_balance(capital), Decimal('60'))
        self.distribution.refresh_from_db()
        self.assertEqual(self.distribution.status, 'DUE')
        second = submit_request(actor=self.holder_user, kind='WITHDRAW_PROFIT', amount=Decimal('40'), currency=self.usd, distribution_id=self.distribution.pk)
        decide_request(actor=self.admin, request_id=second.pk, decision='APPROVED')
        with patch('django.utils.timezone.now', return_value=AUGUST):
            execute_request(actor=self.admin, request_id=second.pk, account_id=self.cash.pk)
        self.distribution.refresh_from_db()
        self.assertEqual(self.distribution.status, 'PAID')
        self.assertEqual(ledger_balance(counterpart(self.usd, AccountType.DISTRIBUTION_PAYABLE, party=self.holder)), Decimal('0'))

    def test_stakeholder_cannot_request_on_someone_else_distribution(self):
        other_distribution = Distribution.objects.get(stakeholder=self.other)
        with self.assertRaises(ValidationError):
            submit_request(actor=self.holder_user, kind='REINVEST_PROFIT', amount=Decimal('10'), currency=self.usd, distribution_id=other_distribution.pk)
        with self.assertRaises(PermissionDenied):
            submit_request(actor=self.agent, kind='REINVEST_PROFIT', amount=Decimal('10'), currency=self.usd, distribution_id=self.distribution.pk)
        self.assertFalse(StakeholderRequest.objects.exists())

    def test_rejection_requires_comment_and_blocks_execution(self):
        request = submit_request(actor=self.holder_user, kind='REINVEST_PROFIT', amount=Decimal('10'), currency=self.usd, distribution_id=self.distribution.pk)
        with self.assertRaises(ValidationError):
            decide_request(actor=self.admin, request_id=request.pk, decision='REJECTED')
        decide_request(actor=self.admin, request_id=request.pk, decision='REJECTED', comment='Not this month')
        with self.assertRaises(ValidationError):
            execute_request(actor=self.admin, request_id=request.pk)
