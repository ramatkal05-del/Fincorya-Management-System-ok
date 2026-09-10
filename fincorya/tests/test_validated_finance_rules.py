"""Regression tests for the validated rules; all balances belong to the test database."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.finance.commissions import choose_commission_destination
from apps.finance.events import counterpart, record_operation
from apps.finance.forms import PolicyForm
from apps.finance.funds import (cancel_transfer, confirm_transfer, confirm_transfer_return, guarantee_balance,
    initiate_transfer, receive_transfer, record_contribution, set_partner_guarantee)
from apps.finance.models import (AccountType, CommissionConversion, DistributionPolicy, FinancialAccount,
    InternalTransfer, JournalBatch, PartnerCommissionChoice)
from apps.finance.policies import create_distribution_policy
from apps.finance.reporting import build_report
from apps.finance.results import distribution_plan, split_amount
from apps.finance.services import ledger_balance
from apps.notifications.models import Notification
from apps.operations.services import create_sent_transfer, create_withdrawal, cancel_operation
from apps.reports.services import generate_finance_report, operation_report_snapshot
from apps.stakeholders.models import Stakeholder
from tests.test_finance_workflows import FinanceScenario, JULY


@override_settings(FINANCE_LEDGER_ENABLED=True, LOCAL_AUTH_BYPASS=False, MFA_ENABLED=False, SECURE_SSL_REDIRECT=False)
class ValidatedFinanceRulesTests(FinanceScenario, TestCase):
    def test_finance_overview_with_unassigned_account_and_htmx(self):
        self.client.force_login(self.admin)
        self.assertIsNone(self.service_account.responsible_user)
        for headers in ({}, {'HTTP_HX_REQUEST': 'true'}):
            response = self.client.get(reverse('finance:overview'), **headers)
            self.assertContains(response, self.service_account.code)
            self.assertContains(response, '—')

    def test_1100_guarantee_2000_ceiling_allows_1500_but_retains_funds_checks(self):
        party = Stakeholder.objects.create(name='Guarantee 1100', type='PARTNER')
        from apps.finance.models import EconomicRule
        EconomicRule.objects.create(stakeholder=party, kind='COMMISSION', value=60,
                                    effective_from=date(2026, 7, 1), created_by=self.admin)
        with patch('django.utils.timezone.now', return_value=JULY):
            set_partner_guarantee(actor=self.admin, stakeholder_id=party.pk, currency=self.usd, per_operation_ceiling=2000)
            record_contribution(actor=self.admin, client_key='g1100', origin='PARTNER_GUARANTEE', stakeholder_id=party.pk,
                amount=1100, currency=self.usd, received_on=JULY.date(), account_id=self.service_account.pk)
            self.assertEqual(guarantee_balance(party, self.usd), 1100)
            # The agent has 1000; guarantee approval does not waive liquidity checks.
            with self.assertRaises(ValidationError):
                create_withdrawal(agent=self.agent, account_id=self.legacy.pk, amount=Decimal('1500'),
                    tariff_schedule=self.tariff, stakeholder=party, commission_owner_confirmed=True, idempotency_key='no-cash')
            op = create_sent_transfer(agent=self.agent, account_id=self.legacy.pk, amount=Decimal('1500'),
                tariff_schedule=self.tariff, stakeholder=party, commission_owner_confirmed=True, idempotency_key='allowed1500')
            self.assertEqual(op.status, 'COMPLETED')
            with self.assertRaises(ValidationError):
                create_sent_transfer(agent=self.agent, account_id=self.legacy.pk, amount=Decimal('2000.01'),
                    tariff_schedule=self.tariff, stakeholder=party, commission_owner_confirmed=True, idempotency_key='over2000')
            set_partner_guarantee(actor=self.admin, stakeholder_id=party.pk, currency=self.usd, per_operation_ceiling=2000, is_active=False)
            with self.assertRaises(ValidationError):
                create_sent_transfer(agent=self.agent, account_id=self.legacy.pk, amount=Decimal('1500'),
                    tariff_schedule=self.tariff, stakeholder=party, commission_owner_confirmed=True, idempotency_key='inactive')

    def destination(self):
        receiver = User.objects.create_user(email='receiver@test.local', role=Role.AGENT)
        from apps.finance.funds import open_agent_cash
        account = open_agent_cash(actor=self.admin, agent=receiver, currency=self.usd)
        return receiver, account

    def test_agent_transfer_http_cycle_and_scoped_access(self):
        receiver, destination = self.destination()
        outsider = User.objects.create_user(email='outsider@test.local', role=Role.AGENT)
        self.client.force_login(self.agent)
        response = self.client.post(reverse('finance:transfer_create'), {'client_key': 'agents', 'source_id': self.cash.pk,
            'destination_id': destination.pk, 'amount': '300', 'fee': '0'})
        self.assertEqual(response.status_code, 302)
        transfer = InternalTransfer.objects.get(client_key='agents')
        self.assertEqual(ledger_balance(self.cash), 700)
        self.assertEqual(ledger_balance(destination), 0)
        self.assertTrue(Notification.objects.filter(recipient=self.admin, subject='Transfert interne initié').exists())
        with self.assertRaises(PermissionDenied):
            initiate_transfer(actor=outsider, client_key='steal', source_id=self.cash.pk, destination_id=destination.pk, amount=100)
        with self.assertRaises(ValidationError):
            initiate_transfer(actor=outsider, client_key='agents', source_id=self.cash.pk, destination_id=destination.pk, amount=300)
        for user in [outsider, self.agent]:
            with self.assertRaises(PermissionDenied):
                receive_transfer(actor=user, transfer_id=transfer.pk)
        self.client.force_login(outsider)
        self.assertNotContains(self.client.get(reverse('finance:workspace', args=['transfers'])), 'agents')
        self.assertEqual(self.client.get(reverse('finance:workspace', args=['contributions'])).status_code, 403)
        self.client.force_login(receiver)
        self.assertEqual(self.client.post(reverse('finance:transfer_action', args=[transfer.pk, 'receive'])).status_code, 302)
        self.assertEqual(ledger_balance(destination), 0)
        for user in [self.agent, receiver, self.manager]:
            with self.assertRaises(PermissionDenied):
                confirm_transfer(actor=user, transfer_id=transfer.pk)
        confirm_transfer(actor=self.admin, transfer_id=transfer.pk)
        confirm_transfer(actor=self.admin, transfer_id=transfer.pk)
        self.assertEqual(ledger_balance(destination), 300)
        destination.legacy_cash_account.refresh_from_db()
        self.assertEqual(destination.legacy_cash_account.balance, 300)

    def test_cancellation_waits_for_documented_return_even_after_receipt(self):
        receiver, destination = self.destination()
        transfer = initiate_transfer(actor=self.agent, client_key='return', source_id=self.cash.pk, destination_id=destination.pk, amount=300)
        receive_transfer(actor=receiver, transfer_id=transfer.pk)
        with self.assertRaises(ValidationError):
            cancel_transfer(actor=self.admin, transfer_id=transfer.pk, reason='Remis physiquement')
        self.assertEqual(ledger_balance(self.cash), 700)
        with self.assertRaises(PermissionDenied):
            confirm_transfer_return(actor=receiver, transfer_id=transfer.pk, note='Retour reçu')
        confirm_transfer_return(actor=self.agent, transfer_id=transfer.pk, note='Retour effectif reçu, reçu RET-001')
        self.assertEqual(ledger_balance(self.cash), 700)
        with self.assertRaises(ValidationError):
            confirm_transfer(actor=self.admin, transfer_id=transfer.pk)
        cancel_transfer(actor=self.admin, transfer_id=transfer.pk, reason='Retour vérifié RET-001')
        cancel_transfer(actor=self.admin, transfer_id=transfer.pk, reason='Retour vérifié RET-001')
        self.assertEqual(ledger_balance(self.cash), 1000)
        self.assertEqual(ledger_balance(destination), 0)

    def test_finance_manager_retains_internal_movement_preparation(self):
        transfer = initiate_transfer(actor=self.manager, client_key='manager', source_id=self.service_account.pk, destination_id=self.cash.pk, amount=100)
        receive_transfer(actor=self.manager, transfer_id=transfer.pk)
        confirm_transfer(actor=self.admin, transfer_id=transfer.pk)
        self.assertEqual(ledger_balance(self.cash), 1100)

    def partner_user(self):
        user = User.objects.create_user(email='choice@test.local', role=Role.PARTNER)
        self.partner.owner = user
        self.partner.save(update_fields=['owner'])
        return user

    def test_automatic_commissions_are_dated_cash_neutral_and_idempotent(self):
        user = self.partner_user()
        old = self.operation('accumulated', partner=self.partner, when=JULY - timedelta(hours=1))
        with patch('django.utils.timezone.now', return_value=JULY):
            choice = choose_commission_destination(actor=user, destination='GUARANTEE')
        op = self.operation('auto', partner=self.partner, when=JULY + timedelta(hours=1))
        second = self.operation('auto2', partner=self.partner, when=JULY + timedelta(hours=2))
        self.assertEqual(CommissionConversion.objects.count(), 2)
        self.assertFalse(CommissionConversion.objects.filter(operation=old).exists())
        self.assertEqual(CommissionConversion.objects.get(operation=op).choice, choice)
        self.assertEqual(guarantee_balance(self.partner, self.usd), 2120)
        payable = counterpart(self.usd, AccountType.PARTNER_PAYABLE, party=self.partner)
        self.assertEqual(ledger_balance(payable), 60)
        treasury = ledger_balance(self.cash)
        self.assertEqual(treasury, 4300)
        with patch('django.utils.timezone.now', return_value=JULY + timedelta(hours=2)):
            record_operation(second, self.agent)
        self.assertEqual(ledger_balance(self.cash), treasury)
        self.assertEqual(CommissionConversion.objects.count(), 2)
        with patch('django.utils.timezone.now', return_value=JULY + timedelta(hours=3)):
            choose_commission_destination(actor=user, destination='PAYABLE')
        self.operation('payable', partner=self.partner, when=JULY + timedelta(hours=4))
        self.assertEqual(ledger_balance(payable), 120)
        self.assertEqual(PartnerCommissionChoice.objects.count(), 2)
        self.assertEqual(guarantee_balance(self.partner, self.usd), 2120)

    def test_commission_choice_future_effect_ownership_and_reversal(self):
        user = self.partner_user()
        with patch('django.utils.timezone.now', return_value=JULY):
            with self.assertRaises(PermissionDenied):
                choose_commission_destination(actor=self.admin, destination='GUARANTEE')
            with self.assertRaises(ValidationError):
                choose_commission_destination(actor=user, destination='GUARANTEE', effective_at=JULY - timedelta(days=1))
            choice = choose_commission_destination(actor=user, destination='GUARANTEE', effective_at=JULY + timedelta(hours=1))
        self.operation('before-choice', partner=self.partner)
        op = self.operation('after-choice', partner=self.partner, when=JULY + timedelta(hours=2))
        with patch('django.utils.timezone.now', return_value=JULY + timedelta(hours=3)):
            cancel_operation(operation_id=op.pk, cancelled_by=self.admin, reason='Correction documentée')
        self.assertEqual(guarantee_balance(self.partner, self.usd), 2000)
        self.assertEqual(ledger_balance(counterpart(self.usd, AccountType.PARTNER_PAYABLE, party=self.partner)), 60)
        conversion = CommissionConversion.objects.get(operation=op)
        self.assertEqual(conversion.batch.status, 'REVERSED')
        choice.destination = 'PAYABLE'
        with self.assertRaises(ValidationError):
            choice.save()

    def test_policy_requires_explicit_selection_and_new_holder_does_not_change_plan(self):
        self.assertFalse(PolicyForm({'mode': 'EQUAL_SHARES', 'effective_from': '2026-07-01'}).is_valid())
        with self.assertRaises(ValidationError):
            create_distribution_policy(actor=self.admin, mode='EQUAL_SHARES', effective_from=date(2026, 8, 1), shares={})
        empty_policy = DistributionPolicy.objects.create(mode='EQUAL_SHARES', effective_from=date(2026, 8, 1), created_by=self.admin)
        august = self.period(start=date(2026, 8, 1), end=date(2026, 8, 31))
        with self.assertRaises(ValidationError):
            distribution_plan(august)  # Empty policy must not fall back to active shareholders.
        holders = [Stakeholder.objects.create(name=f'Explicit {i}', type='SHAREHOLDER') for i in range(4)]
        policy = create_distribution_policy(actor=self.admin, mode='EQUAL_SHARES', effective_from=date(2026, 9, 1),
                                            shares={p.pk: 25 for p in holders})
        september = self.period(start=date(2026, 9, 1), end=date(2026, 9, 30))
        plan = distribution_plan(september)
        self.assertEqual([amount for _, amount in split_amount(100, plan['weights'])], [25] * 4)
        Stakeholder.objects.create(name='New fifth', type='SHAREHOLDER')
        self.assertEqual(distribution_plan(september), plan)
        share = policy.shares.first()
        share.percent = 50
        with self.assertRaises(ValidationError):
            share.save()

    def test_reports_downloads_and_shareholder_global_read_only(self):
        self.operation()
        holder = User.objects.create_user(email='readall@test.local', role=Role.SHAREHOLDER)
        Stakeholder.objects.create(name='Read all', type='SHAREHOLDER', owner=holder)
        self.client.force_login(holder)
        op = self.legacy.operations.first()
        self.assertContains(self.client.get(reverse('operations:list')), str(op.reference))
        self.assertEqual(self.client.get(reverse('operations:detail', args=[op.reference])).status_code, 200)
        self.assertEqual(self.client.get(reverse('operations:create')).status_code, 403)
        self.assertEqual(self.client.post(reverse('operations:cancel', args=[op.reference]), {'reason': 'Not permitted'}).status_code, 403)
        self.assertEqual(len(build_report(user=holder, kind='ACTIVITY', preset='MONTH', anchor=JULY.date())['sections'][-1]['rows']), 1)
        with self.assertRaises(PermissionDenied):
            operation_report_snapshot(user=self.agent, start_date=JULY.date(), end_date=JULY.date() + timedelta(days=1))
        for user, kind, preset in [(self.agent, 'ACTIVITY', 'DAY'), (self.manager, 'ACTIVITY', 'WEEK'), (self.admin, 'ACTIVITY', 'YEAR')]:
            export = generate_finance_report(user=user, kind=kind, preset=preset, anchor=JULY.date(), format='PDF')
            self.assertEqual(export.status, 'READY')
            self.client.force_login(user)
            response = self.client.get(reverse('reports:download', args=[export.public_id]))
            self.assertEqual(response.status_code, 200)
