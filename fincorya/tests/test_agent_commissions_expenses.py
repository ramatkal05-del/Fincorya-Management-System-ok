from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.expenses.models import Expense
from apps.operations.models import Operation
from apps.operations.services import cancel_operation, revise_operation
from apps.pricing.models import Currency, ExchangeRate
from apps.cash.models import CashAccount
from tests.test_finance_workflows import FinanceScenario


@override_settings(FINANCE_LEDGER_ENABLED=True, LOCAL_AUTH_BYPASS=False,
                   MFA_ENABLED=False, SECURE_SSL_REDIRECT=False)
class AgentCommissionExpenseTests(FinanceScenario, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.agent)

    def payload(self, **changes):
        data = dict(type="SENT_TRANSFER", account=self.legacy.pk, service="AIRTEL_MONEY",
                    customer_identifier="+243123456789", customer_name="Commission manuelle",
                    amount="1000", manual_fee="125", fee_mode="ADDED",
                    commission_owner_confirmed="on", idempotency_key="manual-commission")
        data.update(changes)
        return data

    def test_manual_commission_without_published_tariff_and_idempotent_save(self):
        self.tariff.is_published = False
        self.tariff.save()
        data = self.payload(amount="6000")
        preview = self.client.get(reverse("operations:preview"), data)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.context["total"], Decimal("6125"))
        for _ in range(2):
            self.assertEqual(self.client.post(reverse("operations:create"), data).status_code, 302)
        operation = Operation.objects.get(customer_name=data["customer_name"])
        self.assertEqual(operation.fee, Decimal("125"))
        self.assertIsNone(operation.tariff_schedule_id)
        self.legacy.refresh_from_db()
        self.assertEqual(self.legacy.balance, Decimal("7125"))

    def test_preview_and_saved_cash_impact_for_both_modes(self):
        cases = [("SENT_TRANSFER", "ADDED", "100", "112", "100", "112"),
                 ("SENT_TRANSFER", "DEDUCTED", "88", "100", "88", "100"),
                 ("WITHDRAWAL", "ADDED", "100", "112", "100", "-100"),
                 ("WITHDRAWAL", "DEDUCTED", "100", "100", "88", "-88")]
        for index, (kind, mode, principal, total, net, delta) in enumerate(cases):
            with self.subTest(kind=kind, mode=mode):
                self.legacy.refresh_from_db()
                before = self.legacy.balance
                data = self.payload(type=kind, fee_mode=mode, amount="100", manual_fee="12", idempotency_key=f"mode-{index}")
                response = self.client.get(reverse("operations:preview"), data)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["total"], Decimal(total))
                self.assertEqual(response.context["net"], Decimal(net))
                self.assertEqual(self.client.post(reverse("operations:create"), data).status_code, 302)
                operation = Operation.objects.latest("pk")
                self.assertEqual(operation.amount, Decimal(principal))
                self.assertEqual(operation.fee, Decimal("12"))
                self.legacy.refresh_from_db()
                self.assertEqual(self.legacy.balance - before, Decimal(delta))

    def test_invalid_commissions_do_not_create_operations(self):
        for fee in ("", "-1", "NaN", "Infinity", "1.234", "10000000000"):
            with self.subTest(fee=fee):
                data = self.payload(manual_fee=fee)
                self.assertEqual(self.client.get(reverse("operations:preview"), data).status_code, 422)
                response = self.client.post(reverse("operations:create"), data)
                self.assertEqual(response.status_code, 200)
                self.assertIn("manual_fee", response.context["form"].errors)
        self.assertFalse(Operation.objects.exists())
        for kind in ("SENT_TRANSFER", "WITHDRAWAL"):
            data = self.payload(type=kind, manual_fee="1000", fee_mode="DEDUCTED")
            self.assertEqual(self.client.get(reverse("operations:preview"), data).status_code, 422)
            self.assertEqual(self.client.post(reverse("operations:create"), data).status_code, 200)
        self.assertFalse(Operation.objects.exists())

    def test_zero_commission_is_explicitly_allowed(self):
        self.assertEqual(self.client.post(reverse("operations:create"), self.payload(manual_fee="0")).status_code, 302)
        self.assertEqual(Operation.objects.get().fee, 0)

    def test_cancelling_manual_operation_restores_cash(self):
        self.legacy.refresh_from_db()
        before = self.legacy.balance
        self.assertEqual(self.client.post(reverse("operations:create"), self.payload()).status_code, 302)
        operation = Operation.objects.get()
        cancel_operation(operation_id=operation.pk, cancelled_by=self.admin, reason="Erreur de saisie")
        self.legacy.refresh_from_db()
        self.assertEqual(self.legacy.balance, before)

    @override_settings(FINANCE_LEDGER_ENABLED=False)
    def test_revision_without_tariff_preserves_withdrawal_cash_mode(self):
        for index, (mode, expected_delta) in enumerate((("ADDED", "-20"), ("DEDUCTED", "-13"))):
            with self.subTest(mode=mode):
                data = self.payload(type="WITHDRAWAL", amount="100", manual_fee="12", fee_mode=mode,
                                    idempotency_key=f"revise-{index}")
                self.assertEqual(self.client.post(reverse("operations:create"), data).status_code, 302)
                operation = Operation.objects.latest("pk")
                self.legacy.refresh_from_db()
                before = self.legacy.balance
                with patch("apps.finance.cutover.active_cutover", return_value=None):
                    revise_operation(operation_id=operation.pk, changes={"amount": "120", "fee": "19"},
                                     reason="Correction de saisie", revised_by=self.agent)
                self.legacy.refresh_from_db()
                self.assertEqual(self.legacy.balance - before, Decimal(expected_delta))

    def test_local_currency_commission_is_not_treated_as_usd(self):
        cdf = Currency.objects.create(code="CDF")
        ExchangeRate.objects.create(currency=cdf, rate_to_usd=Decimal("0.0004"), created_by=self.admin, effective_at=timezone.now())
        account = CashAccount.objects.create(agent=self.agent, currency=cdf, is_active=True)
        data = self.payload(account=account.pk, amount="250000", manual_fee="1234.56")
        response = self.client.get(reverse("operations:preview"), data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["fee_local"], Decimal("1234.56"))
        self.assertEqual(response.context["fee_usd"], Decimal("0.49"))

    def test_agent_expense_is_scoped_and_cannot_be_self_approved(self):
        other = User.objects.create_user(email="other-agent@test.local", role=Role.AGENT)
        foreign = Expense.objects.create(agent=other, created_by=self.admin, label="Private charge",
                                        amount=25, currency=self.usd, incurred_on="2026-09-01")
        response = self.client.post(reverse("expenses:create"), {
            "category": "OPERATIONS", "agent": other.pk, "label": "Transport agent",
            "amount": "12.50", "currency": self.usd.pk, "incurred_on": "2026-09-01"})
        self.assertEqual(response.status_code, 302)
        expense = Expense.objects.get(label="Transport agent")
        self.assertEqual(expense.agent, self.agent)
        self.assertEqual(expense.created_by, self.agent)
        self.assertEqual(expense.status, "PENDING")
        self.assertContains(self.client.get(reverse("expenses:list")), "Transport agent")
        self.assertNotContains(self.client.get(reverse("expenses:list")), "Private charge")
        self.assertNotContains(self.client.get(reverse("expenses:detail", args=[expense.pk])), "Décision de validation")
        for name in ("detail", "receipt"):
            self.assertEqual(self.client.get(reverse(f"expenses:{name}", args=[foreign.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("expenses:decide", args=[expense.pk]), {"decision": "APPROVED"}).status_code, 403)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("expenses:detail", args=[expense.pk])), "Décision de validation")
        self.assertEqual(self.client.post(reverse("expenses:decide", args=[expense.pk]), {"decision": "APPROVED"}).status_code, 302)
        expense.refresh_from_db()
        self.assertEqual(expense.status, "APPROVED")
