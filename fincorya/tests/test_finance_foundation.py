from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from unittest import skipUnless

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.accounts.permissions import finance_policy
from apps.cash.models import CashAccount, GlobalCashAccount
from apps.cash.services import adjust_global_cash
from apps.finance.models import (
    AccountNature,
    AccountType,
    BatchStatus,
    EntrySide,
    FinancialAccount,
    FinancialPeriod,
    JournalBatch,
    LedgerEntry,
    PeriodStatus,
)
from apps.finance.services import (
    ledger_balance,
    migrate_legacy_opening_balances,
    post_batch,
    prepare_batch,
    reconcile_account,
    reverse_batch,
)
from apps.pricing.models import Currency
from apps.stakeholders.models import EconomicRole, Stakeholder, StakeholderRole, StakeholderType


class FinanceFoundationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(email="admin-finance@test.local", password="test", role=Role.ADMIN)
        cls.manager = User.objects.create_user(email="mpoto@test.local", password="test", role=Role.FINANCE_MANAGER)
        cls.agent = User.objects.create_user(email="agent-finance@test.local", password="test", role=Role.AGENT)
        cls.usd = Currency.objects.create(code="USD")
        cls.cash = FinancialAccount.objects.create(code="CASH-TEST", name="Caisse test", account_type=AccountType.AGENT_CASH, nature=AccountNature.ASSET, currency=cls.usd, responsible_user=cls.agent)
        cls.capital = FinancialAccount.objects.create(code="CAPITAL-TEST", name="Capital test", account_type=AccountType.CAPITAL, nature=AccountNature.EQUITY, currency=cls.usd)

    def lines(self, amount=Decimal("100.00")):
        return [
            {"account": self.cash, "side": EntrySide.DEBIT, "amount": amount, "currency": self.usd},
            {"account": self.capital, "side": EntrySide.CREDIT, "amount": amount, "currency": self.usd},
        ]

    def test_access_role_is_separate_from_economic_roles(self):
        ruth = Stakeholder.objects.create(name="Ruth Ngomo", type=StakeholderType.SHAREHOLDER, owner=self.agent)
        StakeholderRole.objects.create(stakeholder=ruth, role=EconomicRole.SHAREHOLDER, effective_from=date.today())
        StakeholderRole.objects.create(stakeholder=ruth, role=EconomicRole.AGENT, effective_from=date.today())
        self.assertTrue(finance_policy(self.agent).own_cash_only)
        self.assertFalse(finance_policy(self.agent).approve)
        self.assertEqual(ruth.economic_roles.count(), 2)

    def test_finance_manager_prepares_but_cannot_post(self):
        batch = prepare_batch(actor=self.manager, idempotency_key="manager-prepares", event_type="CAPITAL", effective_at=timezone.now(), description="Apport", lines=self.lines())
        with self.assertRaises(PermissionDenied):
            post_batch(batch_id=batch.pk, actor=self.manager)

    def test_balanced_batch_updates_cache_and_reconciles(self):
        batch = prepare_batch(actor=self.admin, idempotency_key="balanced", event_type="CAPITAL", effective_at=timezone.now(), description="Apport", lines=self.lines())
        post_batch(batch_id=batch.pk, actor=self.admin)
        self.cash.refresh_from_db()
        self.capital.refresh_from_db()
        self.assertEqual(self.cash.cached_balance, Decimal("100.00"))
        self.assertEqual(self.capital.cached_balance, Decimal("100.00"))
        self.assertEqual(ledger_balance(self.cash), Decimal("100.00"))
        self.assertEqual(reconcile_account(self.cash)["difference"], Decimal("0.00"))

    def test_unbalanced_batch_is_rejected_without_balance_change(self):
        lines = self.lines()
        lines[1]["amount"] = Decimal("99.00")
        batch = prepare_batch(actor=self.admin, idempotency_key="unbalanced", event_type="CAPITAL", effective_at=timezone.now(), description="Invalide", lines=lines)
        with self.assertRaises(ValidationError):
            post_batch(batch_id=batch.pk, actor=self.admin)
        self.cash.refresh_from_db()
        self.assertEqual(self.cash.cached_balance, 0)

    def test_posting_is_idempotent_and_posted_rows_are_immutable(self):
        batch = prepare_batch(actor=self.admin, idempotency_key="once", event_type="CAPITAL", effective_at=timezone.now(), description="Unique", lines=self.lines())
        post_batch(batch_id=batch.pk, actor=self.admin)
        post_batch(batch_id=batch.pk, actor=self.admin)
        self.cash.refresh_from_db()
        self.assertEqual(self.cash.cached_balance, Decimal("100.00"))
        entry = batch.entries.first()
        entry.memo = "altération"
        with self.assertRaises(ValidationError):
            entry.save()
        with self.assertRaises(ValidationError):
            batch.entries.update(memo="altération")

    def test_reversal_uses_counter_entries(self):
        batch = prepare_batch(actor=self.admin, idempotency_key="to-reverse", event_type="CAPITAL", effective_at=timezone.now(), description="Apport", lines=self.lines())
        post_batch(batch_id=batch.pk, actor=self.admin)
        reverse_batch(batch_id=batch.pk, actor=self.admin, reason="Apport annulé", idempotency_key="reverse-once")
        self.cash.refresh_from_db()
        batch.refresh_from_db()
        self.assertEqual(batch.status, BatchStatus.REVERSED)
        self.assertEqual(self.cash.cached_balance, Decimal("0.00"))
        self.assertEqual(ledger_balance(self.cash), Decimal("0.00"))

    def test_posted_batch_rejects_new_entries_and_stale_instance_mutations(self):
        batch = prepare_batch(actor=self.admin, idempotency_key="immutable-stale", event_type="CAPITAL", effective_at=timezone.now(), description="Apport", lines=self.lines())
        entry = batch.entries.select_related("batch").first()
        post_batch(batch_id=batch.pk, actor=self.admin)
        with self.assertRaises(ValidationError):
            LedgerEntry.objects.create(batch=batch, **self.lines()[0])
        entry.memo = "altération"
        with self.assertRaises(ValidationError):
            entry.save()
        with self.assertRaises(ValidationError):
            entry.delete()
        draft = prepare_batch(actor=self.admin, idempotency_key="another-draft", event_type="CAPITAL", effective_at=timezone.now(), description="Brouillon", lines=self.lines())
        entry.batch = draft
        with self.assertRaises(ValidationError):
            entry.save()
        self.assertEqual(batch.entries.count(), 2)

    def test_locked_period_blocks_posting(self):
        from apps.finance.locking import save_internal
        today = date.today()
        period = FinancialPeriod.objects.create(period_type="DAY", start_date=today, end_date=today)
        period.status = PeriodStatus.LOCKED
        with self.assertRaises(ValidationError):
            period.save()
        # Set up an already closed period without bypassing the posting guard.
        save_internal(period)
        batch = prepare_batch(actor=self.admin, idempotency_key="locked", event_type="CAPITAL", effective_at=timezone.now(), description="Apport", lines=self.lines())
        with self.assertRaises(ValidationError):
            post_batch(batch_id=batch.pk, actor=self.admin)

    def test_preview_is_read_only_and_apply_requires_feature_flag(self):
        global_cash = GlobalCashAccount.objects.create(administrator=self.admin, currency=self.usd)
        legacy = CashAccount.objects.create(agent=self.agent, currency=self.usd, global_account=global_cash)
        adjust_global_cash(global_account_id=global_cash.pk, direction="IN", amount=Decimal("50"), adjusted_by=self.admin, note="Test")
        from apps.cash.services import allocate_cash
        allocate_cash(account_id=legacy.pk, amount=Decimal("50"), allocated_by=self.admin, note="Test")
        result = migrate_legacy_opening_balances(actor=self.admin, cutover_at=timezone.now(), apply=False)
        self.assertFalse(result["applied"])
        self.assertEqual(JournalBatch.objects.count(), 0)
        with self.assertRaises(ValidationError):
            migrate_legacy_opening_balances(actor=self.admin, cutover_at=timezone.now(), apply=True)

    def test_opening_only_migration_reconciles_without_importing_history(self):
        global_cash = GlobalCashAccount.objects.create(administrator=self.admin, currency=self.usd)
        legacy = CashAccount.objects.create(agent=self.agent, currency=self.usd, global_account=global_cash)
        adjust_global_cash(global_account_id=global_cash.pk, direction="IN", amount=Decimal("75"), adjusted_by=self.admin, note="Test")
        from apps.cash.services import allocate_cash
        allocate_cash(account_id=legacy.pk, amount=Decimal("75"), allocated_by=self.admin, note="Test")
        cutover_at = timezone.now()
        preview = migrate_legacy_opening_balances(actor=self.admin, cutover_at=cutover_at)
        with override_settings(FINANCE_LEDGER_ENABLED=True):
            with self.assertRaises(ValidationError):
                migrate_legacy_opening_balances(actor=self.admin, cutover_at=cutover_at, apply=True, expected_hash="stale")
            run = migrate_legacy_opening_balances(actor=self.admin, cutover_at=cutover_at, apply=True, expected_hash=preview["preview_hash"])
        row = run.reconciliations.get(account__legacy_cash_account=legacy)
        self.assertEqual(row.legacy_balance, Decimal("75.00"))
        self.assertEqual(row.migrated_balance, Decimal("75.00"))
        self.assertEqual(row.difference, Decimal("0.00"))
        self.assertEqual(JournalBatch.objects.filter(event_type="LEGACY_OPENING").count(), 1)

    def test_inactive_funded_cash_remains_in_period_totals_and_count_controls(self):
        from apps.finance.closing import period_report, record_count
        from apps.finance.forms import CountForm, PaymentForm
        batch = prepare_batch(actor=self.admin, idempotency_key="inactive-cash", event_type="CAPITAL", effective_at=timezone.now(), description="Apport", lines=self.lines())
        post_batch(batch_id=batch.pk, actor=self.admin)
        self.cash.refresh_from_db()
        self.cash.is_active = False
        self.cash.save(update_fields=["is_active"])
        self.assertIn(self.cash, CountForm().fields["account_id"].queryset)
        self.assertNotIn(self.cash, PaymentForm().fields["account_id"].queryset)
        today = timezone.localdate()
        period = FinancialPeriod.objects.create(period_type="DAY", start_date=today, end_date=today)
        report = period_report(period)
        self.assertEqual(report["totals"]["USD"]["cash"], Decimal("100.00"))
        self.assertIn("CASH-TEST : compte non compté", report["controls"])
        record_count(period_id=period.pk, account_id=self.cash.pk, declared=Decimal("100.00"), justification="", actor=self.admin)
        self.assertEqual(period_report(period)["controls"], [])


@skipUnless(connection.vendor == "postgresql", "Verrouillage concurrent vérifiable uniquement sur PostgreSQL")
class PostgreSQLPostingConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_same_batch_cannot_be_posted_twice_concurrently(self):
        admin = User.objects.create_user(email="postgres-admin@test.local", password="test", role=Role.ADMIN)
        usd = Currency.objects.create(code="USD")
        cash = FinancialAccount.objects.create(code="PG-CASH", name="PG Cash", account_type=AccountType.AGENT_CASH, nature=AccountNature.ASSET, currency=usd)
        capital = FinancialAccount.objects.create(code="PG-CAPITAL", name="PG Capital", account_type=AccountType.CAPITAL, nature=AccountNature.EQUITY, currency=usd)
        batch = prepare_batch(actor=admin, idempotency_key="pg-concurrent", event_type="CAPITAL", effective_at=timezone.now(), description="Concurrent", lines=[
            {"account": cash, "side": EntrySide.DEBIT, "amount": Decimal("10"), "currency": usd},
            {"account": capital, "side": EntrySide.CREDIT, "amount": Decimal("10"), "currency": usd},
        ])
        with ThreadPoolExecutor(max_workers=2) as pool:
            from tests.test_finance_workflows import in_thread
            list(pool.map(lambda _: in_thread(post_batch, batch_id=batch.pk, actor=admin), range(2)))
        cash.refresh_from_db()
        self.assertEqual(cash.cached_balance, Decimal("10.00"))
