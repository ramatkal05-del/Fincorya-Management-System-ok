"""End-to-end validation of new admin onboarding, agent cash, FX and remuneration paths."""
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from zoneinfo import ZoneInfo

from apps.accounts.models import Role, User
from apps.cash.models import CashAccount
from apps.cash.services import adjust_global_cash, allocate_cash
from apps.expenses.models import Expense
from apps.finance.closing import close_period, propose_distribution, approve_distributions
from apps.finance.events import counterpart
from apps.finance.funds import record_conversion, open_agent_cash
from apps.finance.models import AccountType, DistributionPolicy, EconomicRule, FinancialAccount, FinancialPeriod, RemunerationTerms
from apps.finance.results import distribution_plan
from apps.finance.services import ledger_balance
from apps.pricing.models import Currency, ExchangeRate
from apps.profits.models import Distribution, ProfitPeriod
from apps.stakeholders.models import Stakeholder
from tests.test_finance_workflows import AUGUST, FinanceScenario, JULY, ZONE


@override_settings(FINANCE_LEDGER_ENABLED=True, LOCAL_AUTH_BYPASS=False, MFA_ENABLED=False, SECURE_SSL_REDIRECT=False)
class OnboardingAndConversionE2ETests(FinanceScenario, TestCase):
    def test_admin_onboards_partner_with_guarantee_and_deposit(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('finance:party_onboarding'), {
            'party_type': 'PARTNER', 'name': 'E2E Partner', 'email': 'e2e@partner.local',
            'currency': self.usd.pk, 'per_operation_ceiling': '1200',
            'client_key': 'e2e-partner-1', 'deposit_amount': '800',
            'deposit_received_on': '2026-07-10', 'deposit_account': self.service_account.pk,
            'notes': 'Contrat E2E'})
        self.assertEqual(response.status_code, 302)
        party = Stakeholder.objects.get(name='E2E Partner')
        self.assertEqual(party.type, 'PARTNER')
        self.assertEqual(party.guarantee.per_operation_ceiling, Decimal('1200'))
        self.assertEqual(ledger_balance(counterpart(self.usd, AccountType.PARTNER_GUARANTEE, party=party)), Decimal('800'))

    def test_admin_onboards_investor_without_ceiling_and_deposit_from_opening(self):
        opening = FinancialAccount.objects.get(code='OPENING-EQUITY-USD')
        before = ledger_balance(opening)
        self.client.force_login(self.admin)
        response = self.client.post(reverse('finance:party_onboarding'), {
            'party_type': 'INVESTOR', 'name': 'E2E Investor', 'email': 'e2e@investor.local',
            'currency': self.usd.pk, 'client_key': 'e2e-investor-1',
            'deposit_amount': '300', 'deposit_received_on': '2026-07-10',
            'deposit_account': self.service_account.pk, 'deposit_from_opening': 'on'})
        self.assertEqual(response.status_code, 302)
        investor = Stakeholder.objects.get(name='E2E Investor')
        self.assertEqual(investor.type, 'INVESTOR')
        self.assertEqual(ledger_balance(counterpart(self.usd, AccountType.INVESTOR_FUNDS, party=investor)), Decimal('300'))
        self.assertEqual(ledger_balance(opening), before - Decimal('300'))

    def test_open_agent_cash_creates_empty_account_and_allocation_is_traced(self):
        agent2 = User.objects.create_user(email='agent2@test.local', role=Role.AGENT)
        with patch('django.utils.timezone.now', return_value=JULY):
            account = open_agent_cash(actor=self.admin, agent=agent2, currency=self.usd)
            self.assertEqual(account.account_type, AccountType.AGENT_CASH)
            self.assertEqual(ledger_balance(account), Decimal('0'))
            legacy = CashAccount.objects.get(agent=agent2, currency=self.usd)
            allocate_cash(account_id=legacy.pk, amount=Decimal('400'), allocated_by=self.admin, note='E2E allocation')
        self.assertEqual(ledger_balance(account), Decimal('400'))

    def test_conversion_records_two_amounts_and_realized_difference(self):
        eur = Currency.objects.create(code='EUR')
        eur_account = FinancialAccount.objects.create(code='MPESA-EUR-1', name='M-Pesa EUR', account_type='MOBILE_MONEY', nature='ASSET', currency=eur)
        ExchangeRate.objects.create(currency=self.usd, rate_to_usd=Decimal('1'), effective_at=JULY, created_by=self.admin)
        ExchangeRate.objects.create(currency=eur, rate_to_usd=Decimal('1.1'), effective_at=JULY, created_by=self.admin)
        with patch('django.utils.timezone.now', return_value=JULY):
            conversion = record_conversion(actor=self.admin, client_key='e2e-fx-1', source_id=self.service_account.pk,
                destination_id=eur_account.pk, amount_source=Decimal('100'), amount_destination=Decimal('90'), fee_source=Decimal('1'))
        self.assertEqual(conversion.amount_source, Decimal('100'))
        self.assertEqual(conversion.amount_destination, Decimal('90'))
        # Écart réalisé ≈ 90 - 100 × (1 / 1.1) = -0,91 EUR, isolé hors du distribuable.
        self.assertEqual(conversion.realized_difference, Decimal('-0.91'))


@override_settings(FINANCE_LEDGER_ENABLED=True, LOCAL_AUTH_BYPASS=False, MFA_ENABLED=False, SECURE_SSL_REDIRECT=False)
class InvestorRemunerationE2ETests(FinanceScenario, TestCase):
    def setUp(self):
        super().setUp()
        self.investor = Stakeholder.objects.create(name='Investor E2E', type='INVESTOR')
        EconomicRule.objects.create(stakeholder=self.investor, kind='REMUNERATION', value=Decimal('40'), currency=self.usd,
            effective_from=date(2026, 7, 1), created_by=self.admin)

    def test_close_blocks_without_contractual_terms(self):
        self.operation()
        self.expense('80')  # mois déficitaire par rapport à la rémunération due (net 20 < 40)
        month = self.period()
        self.count_all(month)
        with patch('django.utils.timezone.now', return_value=AUGUST):
            with self.assertRaisesMessage(Exception, 'définissez la règle contractuelle'):
                close_period(period_id=month.pk, actor=self.admin)

    def test_remuneration_accrued_once_and_deducted_from_result(self):
        RemunerationTerms.objects.create(stakeholder=self.investor, contract_start=date(2026, 7, 1),
            partial_month='PRORATA', loss_month='PAY', updated_by=self.admin)
        self.operation()
        month = self.period()
        self.count_all(month)
        with patch('django.utils.timezone.now', return_value=AUGUST):
            close_period(period_id=month.pk, actor=self.admin)
        salary = Expense.objects.get(stakeholder=self.investor, category='INVESTOR_RETURN')
        self.assertEqual(salary.amount, Decimal('40'))
        profit = ProfitPeriod.objects.get(finance_period=month)
        self.assertEqual(profit.net_profit, Decimal('60'))
        self.assertEqual(profit.distributable, Decimal('60'))

    def test_remuneration_accrual_is_idempotent(self):
        RemunerationTerms.objects.create(stakeholder=self.investor, contract_start=date(2026, 7, 1),
            partial_month='PRORATA', loss_month='PAY', updated_by=self.admin)
        self.operation()
        month = self.period()
        self.count_all(month)
        with patch('django.utils.timezone.now', return_value=AUGUST):
            close_period(period_id=month.pk, actor=self.admin)
            close_period(period_id=month.pk, actor=self.admin)
        self.assertEqual(Expense.objects.filter(stakeholder=self.investor, category='INVESTOR_RETURN').count(), 1)

    def test_partial_reinvestment_does_not_count_distribution_twice(self):
        RemunerationTerms.objects.create(stakeholder=self.investor, contract_start=date(2026, 7, 1),
            partial_month='PRORATA', loss_month='PAY', updated_by=self.admin)
        shareholder = Stakeholder.objects.create(name='Holder E2E', type='SHAREHOLDER')
        policy = DistributionPolicy.objects.create(mode='EQUAL_SHARES', effective_from=date(2026, 7, 1), created_by=self.admin)
        from apps.finance.locking import save_internal
        from apps.finance.models import DistributionPolicyShare
        save_internal(DistributionPolicyShare(policy=policy, stakeholder=shareholder, percent=Decimal('100')))
        self.operation()
        month = self.period()
        self.count_all(month)
        with patch('django.utils.timezone.now', return_value=AUGUST):
            close_period(period_id=month.pk, actor=self.admin)
            profit = ProfitPeriod.objects.get(finance_period=month)
            propose_distribution(profit_id=profit.pk, amount=Decimal('60'), actor=self.admin)
            approve_distributions(profit_id=profit.pk, amount=Decimal('60'), actor=self.admin)
        dist = Distribution.objects.get(stakeholder=shareholder)
        # Withdraw 30 then reinvest 30 : remaining 0 and one capital credit of 30
        from apps.finance.funds import submit_request, decide_request, execute_request
        from apps.profits.models import DistributionStatus
        cash_before = ledger_balance(self.cash)
        capital = counterpart(self.usd, AccountType.CAPITAL, party=shareholder)
        req1 = submit_request(actor=self.admin, kind='WITHDRAW_PROFIT', amount=Decimal('30'), currency=self.usd, stakeholder_id=shareholder.pk, distribution_id=dist.pk)
        decide_request(actor=self.admin, request_id=req1.pk, decision='APPROVED')
        with patch('django.utils.timezone.now', return_value=AUGUST):
            execute_request(actor=self.admin, request_id=req1.pk, account_id=self.cash.pk)
        req2 = submit_request(actor=self.admin, kind='REINVEST_PROFIT', amount=Decimal('30'), currency=self.usd, stakeholder_id=shareholder.pk, distribution_id=dist.pk)
        decide_request(actor=self.admin, request_id=req2.pk, decision='APPROVED')
        with patch('django.utils.timezone.now', return_value=AUGUST):
            execute_request(actor=self.admin, request_id=req2.pk)
        dist.refresh_from_db()
        self.assertEqual(dist.status, DistributionStatus.PAID if dist.amount == 60 else DistributionStatus.DUE)
        self.assertEqual(ledger_balance(self.cash), cash_before - 30)
        self.assertEqual(ledger_balance(capital), Decimal('30'))
