from io import StringIO
from datetime import date
from django.test import TestCase, override_settings
from django.core.management import call_command, CommandError
from apps.accounts.models import User
from apps.pricing.models import Currency
from apps.cash.models import CashAccount, CashMovement
from apps.expenses.models import Expense
from apps.finance.models import ImportRow, FinancialPeriod


@override_settings(FINANCE_LEDGER_ENABLED=False, RUN_INLINE_NOTIFICATION_WORKER=False)
class OctoberRestartTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(email='admin@example.test', password=None, role='ADMIN')
        self.usd = Currency.objects.create(code='USD')
        self.options = {'admin': self.admin.email}
        for name in ('victoria', 'david', 'chris', 'grace'):
            user = User.objects.create_user(email=name+'@example.test', password=None, role='AGENT')
            CashAccount.objects.create(agent=user, currency=self.usd)
            self.options[name] = user.pk

    def run_command(self, **kwargs):
        return call_command('prepare_october_restart', stdout=StringIO(), **self.options, **kwargs)

    def test_preview_then_apply_and_replay(self):
        self.run_command()
        self.assertFalse(CashMovement.objects.exists())
        self.assertFalse(Expense.objects.exists())
        self.assertFalse(FinancialPeriod.objects.exists())
        self.run_command(apply=True)
        self.assertEqual(sum(CashAccount.objects.values_list('balance', flat=True)), 7993)
        self.assertEqual(sum(Expense.objects.values_list('amount', flat=True)), 141)
        self.assertEqual(Expense.objects.filter(status='APPROVED').count(), 2)
        self.run_command(apply=True)
        self.assertEqual(CashMovement.objects.count(), 4)
        self.assertEqual(Expense.objects.count(), 2)
        self.assertEqual(ImportRow.objects.count(), 1)

    def test_existing_expense_blocks_all_changes(self):
        Expense.objects.create(label='MoneyGram', amount=126, currency=self.usd,
            incurred_on=date(2026,10,4), created_by=self.admin)
        with self.assertRaises(CommandError):
            self.run_command(apply=True)
        self.assertFalse(CashMovement.objects.exists())
        self.assertFalse(FinancialPeriod.objects.exists())

    def test_duplicate_agent_blocks_all_changes(self):
        self.options['david'] = self.options['victoria']
        with self.assertRaises(CommandError):
            self.run_command(apply=True)
        self.assertFalse(CashMovement.objects.exists())

    def test_real_emails_resolve_without_ids(self):
        from apps.finance.management.commands.prepare_october_restart import EMAILS
        for name, email in EMAILS.items():
            User.objects.filter(pk=self.options[name]).update(email=email)
        call_command('prepare_october_restart', admin=self.admin.email, stdout=StringIO())
        self.assertFalse(CashMovement.objects.exists())

    def test_unknown_email_blocks_all_changes(self):
        User.objects.filter(pk=self.options['victoria']).update(email='someone-else@example.test')
        del self.options['victoria']
        with self.assertRaises(CommandError):
            self.run_command(apply=True)
        self.assertFalse(CashMovement.objects.exists())
