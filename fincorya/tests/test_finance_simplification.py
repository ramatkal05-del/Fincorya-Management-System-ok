from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings

from apps.finance.calculations import partner_commissions
from apps.finance.funds import record_contribution
from tests.test_finance_funds import FundOriginTests


class CommissionCalculationTests(SimpleTestCase):
    def test_frozen_rates_and_per_operation_rounding(self):
        rows = [SimpleNamespace(operation=SimpleNamespace(fee=Decimal('100')), share_percent=Decimal('60')),
                SimpleNamespace(operation=SimpleNamespace(fee=Decimal('100')), share_percent=Decimal('70'))]
        self.assertEqual(partner_commissions(rows), {
            'partner_gross': Decimal('200'), 'partner_share': Decimal('130.00'),
            'fincorya_share': Decimal('70.00')})

    def test_empty_period(self):
        self.assertEqual(partner_commissions([])['fincorya_share'], Decimal('0'))


from tests.test_finance_workflows import FinanceScenario


@override_settings(FINANCE_LEDGER_ENABLED=True, LOCAL_AUTH_BYPASS=False, MFA_ENABLED=False, SECURE_SSL_REDIRECT=False)
class ContributionReplayTests(FinanceScenario, TestCase):
    contribute = FundOriginTests.contribute

    def setUp(self):
        super().setUp()
        from apps.stakeholders.models import Stakeholder
        self.shareholder = Stakeholder.objects.create(name="Replay shareholder", type="SHAREHOLDER")

    def test_replay_cannot_change_receipt_date_or_destination(self):
        receipt = self.contribute()
        data = dict(actor=self.admin, client_key=receipt.client_key, origin=receipt.origin,
                    stakeholder_id=receipt.stakeholder_id, amount=receipt.amount,
                    currency=self.usd, received_on=date(2026, 7, 16), account_id=self.service_account.pk)
        with self.assertRaises(ValidationError):
            record_contribution(**data)
        data.update(received_on=receipt.received_on, account_id=self.cash.pk)
        with self.assertRaises(ValidationError):
            record_contribution(**data)

    def test_reference_blocks_duplicate_under_another_client_key(self):
        data = dict(actor=self.admin, client_key='reference-first', origin='SHAREHOLDER',
                    stakeholder_id=self.shareholder.pk, amount=Decimal('50'), currency=self.usd,
                    received_on=date(2026, 7, 15), account_id=self.service_account.pk,
                    external_reference=' bank-123 ')
        first = record_contribution(**data)
        self.assertEqual(first.external_reference, 'bank-123')
        self.assertEqual(record_contribution(**data).pk, first.pk)
        data['client_key'] = 'reference-second'
        with self.assertRaises(ValidationError):
            record_contribution(**data)

    def test_legacy_receipt_cannot_be_received_twice(self):
        from apps.stakeholders.models import Investment
        from apps.finance.services import ledger_balance
        old = Investment.objects.create(stakeholder=self.shareholder, amount=50,
            currency=self.usd, invested_on=date(2026, 7, 15))
        before = ledger_balance(self.service_account)
        data = dict(actor=self.admin, client_key="legacy-first", origin="SHAREHOLDER",
            stakeholder_id=self.shareholder.pk, amount=Decimal("50"), currency=self.usd,
            received_on=old.invested_on, legacy_investment_id=old.pk,
            account_id=self.service_account.pk)
        with self.assertRaises(ValidationError):
            record_contribution(**data)
        data.update(from_opening_balances=True, account_id=None)
        receipt = record_contribution(**data)
        self.assertEqual(receipt.legacy_investment_id, old.pk)
        self.assertEqual(ledger_balance(self.service_account), before)
        data["client_key"] = "legacy-second"
        with self.assertRaises(ValidationError):
            record_contribution(**data)

    def test_new_policy_rejects_capital_weighting_and_unequal_shares(self):
        from apps.finance.policies import create_distribution_policy
        from apps.stakeholders.models import Stakeholder
        holders = [Stakeholder.objects.create(name=f"Policy {i}", type="SHAREHOLDER") for i in range(4)]
        shares = {p.pk: 25 for p in holders}
        with self.assertRaises(ValidationError):
            create_distribution_policy(actor=self.admin, mode="CAPITAL_PROPORTIONAL",
                effective_from=date(2026, 9, 1), shares=shares)
        policy = create_distribution_policy(actor=self.admin, mode="EQUAL_SHARES",
            effective_from=date(2026, 9, 1), shares=shares)
        self.assertEqual(policy.shares.count(), 4)
