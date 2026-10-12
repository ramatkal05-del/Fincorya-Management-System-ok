import json
from datetime import date
from io import StringIO

from django.core.management import call_command, CommandError
from django.test import TestCase

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.finance.locking import save_internal
from apps.finance.models import FinancialPeriod, ImportRow
from apps.pricing.models import Currency
from apps.stakeholders.models import Investment, Stakeholder


class OctoberPeriodRepairTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(email="admin@example.test", role="ADMIN")
        usd = Currency.objects.create(code="USD")
        party = Stakeholder.objects.create(name="Glodie", type="SHAREHOLDER")
        self.investment = Investment.objects.create(stakeholder=party, currency=usd,
                                                   amount=4370, invested_on=date(2026, 10, 1))
        self.source = ImportRow.objects.create(source_key="RAPPORT-2-OCTOBRE-2026",
                                               payload={"investments": [self.investment.pk]})

    def run_command(self, **options):
        output = StringIO()
        call_command("repair_october_period", admin=self.admin.email, stdout=output, **options)
        return json.loads(output.getvalue())

    def test_preview_apply_and_replay_preserve_imported_funds(self):
        self.assertEqual(self.run_command()["period_action"], "CREATION")
        self.assertFalse(FinancialPeriod.objects.exists())
        first = self.run_command(apply=True)
        second = self.run_command(apply=True)
        self.assertEqual(first["period_id"], second["period_id"])
        self.assertEqual(FinancialPeriod.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action="OCTOBER_PERIOD_REPAIR").count(), 1)
        period = FinancialPeriod.objects.get()
        self.assertEqual((period.period_type, period.start_date, period.end_date, period.status),
                         ("MONTH", date(2026, 10, 1), date(2026, 10, 31), "OPEN"))
        self.investment.refresh_from_db()
        self.source.refresh_from_db()
        self.assertEqual(Investment.objects.count(), 1)
        self.assertEqual(self.investment.amount, 4370)
        self.assertEqual(self.source.payload, {"investments": [self.investment.pk]})

    def test_conflicting_month_blocks_creation(self):
        FinancialPeriod.objects.create(period_type="MONTH", start_date=date(2026, 9, 30),
                                        end_date=date(2026, 10, 30))
        with self.assertRaises(CommandError):
            self.run_command(apply=True)
        self.assertEqual(FinancialPeriod.objects.count(), 1)
        self.assertFalse(AuditEvent.objects.filter(action="OCTOBER_PERIOD_REPAIR").exists())

    def test_locked_week_blocks_new_month(self):
        period = FinancialPeriod(period_type="WEEK", start_date=date(2026, 10, 1),
                                 end_date=date(2026, 10, 11), status="LOCKED")
        save_internal(period)
        with self.assertRaises(CommandError):
            self.run_command(apply=True)
        self.assertEqual(FinancialPeriod.objects.count(), 1)

    def test_open_week_is_preserved(self):
        week = FinancialPeriod.objects.create(period_type="WEEK", start_date=date(2026, 10, 1),
                                               end_date=date(2026, 10, 11))
        self.run_command(apply=True)
        week.refresh_from_db()
        self.assertEqual(week.end_date, date(2026, 10, 11))
        self.assertEqual(FinancialPeriod.objects.count(), 2)

    def test_locked_existing_month_is_not_reopened(self):
        period = FinancialPeriod(period_type="MONTH", start_date=date(2026, 10, 1),
                                 end_date=date(2026, 10, 31), status="LOCKED")
        save_internal(period)
        self.assertEqual(self.run_command(apply=True)["period_status"], "LOCKED")
        self.assertFalse(AuditEvent.objects.filter(action="OCTOBER_PERIOD_REPAIR").exists())
