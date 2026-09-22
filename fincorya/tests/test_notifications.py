from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import AccountActivationToken, Role, User
from apps.accounts.services import consume_activation_token, get_valid_activation_token, issue_activation_token
from apps.expenses.models import Expense, ExpenseCategory, ExpenseStatus
from apps.expenses.services import decide_expense
from apps.notifications.models import Notification, NotificationDelivery, NotificationSettings
from apps.notifications.senders import send_salary_paid, send_weekly_agent_reminder
from apps.notifications.services import notify
from apps.pricing.models import Currency


@override_settings(MFA_ENABLED=False, LOCAL_AUTH_BYPASS=False,
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class NotificationDeduplicationTests(TestCase):
    def setUp(self):
        self.agent = User.objects.create_user(email="agent@notif.test", role=Role.AGENT)

    def test_same_event_key_never_creates_a_second_notification(self):
        notify(recipient=self.agent, subject="Sujet", body="Corps", event_key="unit-test-key")
        notify(recipient=self.agent, subject="Sujet 2", body="Corps 2", event_key="unit-test-key")
        self.assertEqual(Notification.objects.filter(event_key="unit-test-key").count(), 1)
        self.assertEqual(Notification.objects.get(event_key="unit-test-key").subject, "Sujet")

    def test_notification_without_event_key_can_repeat(self):
        notify(recipient=self.agent, subject="Sujet", body="Corps")
        notify(recipient=self.agent, subject="Sujet", body="Corps")
        self.assertEqual(Notification.objects.filter(recipient=self.agent, subject="Sujet").count(), 2)

    def test_inactive_recipient_is_persisted_but_not_queued_for_delivery(self):
        self.agent.is_active = False
        self.agent.save(update_fields=["is_active"])
        notification = notify(recipient=self.agent, subject="Sujet", body="Corps", event_key="inactive-key")
        self.assertFalse(NotificationDelivery.objects.filter(notification=notification).exists())


@override_settings(MFA_ENABLED=False, LOCAL_AUTH_BYPASS=False,
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"], BUSINESS_TIME_ZONE="Europe/Istanbul")
class WeeklyAgentReminderTests(TestCase):
    def setUp(self):
        self.agent = User.objects.create_user(email="agent2@notif.test", role=Role.AGENT)
        NotificationSettings.objects.update_or_create(pk=1, defaults={
            "weekly_closure_weekday": 6, "weekly_closure_time": time(18, 0), "reminder_hours_before": 2,
        })

    def _run(self, now):
        with override_settings():
            from django.utils import timezone as tz
            import unittest.mock as mock
            with mock.patch.object(tz, "now", return_value=now):
                from django.core.management import call_command
                call_command("send_weekly_agent_reminders", "--window-minutes", "5")

    def test_reminder_sent_exactly_two_hours_before_closure(self):
        zone = ZoneInfo("Europe/Istanbul")
        # Closure configured for Sunday 18:00; reminder window is [16:00-Δ, 16:00+Δ].
        sunday = date(2026, 9, 6)  # a Sunday
        reminder_time = datetime.combine(sunday, time(16, 0), tzinfo=zone)
        self._run(reminder_time)
        self.assertTrue(Notification.objects.filter(recipient=self.agent, category="WEEKLY_AGENT_REMINDER").exists())

    def test_reminder_is_not_sent_outside_the_window(self):
        zone = ZoneInfo("Europe/Istanbul")
        sunday = date(2026, 9, 6)
        too_early = datetime.combine(sunday, time(10, 0), tzinfo=zone)
        self._run(too_early)
        self.assertFalse(Notification.objects.filter(recipient=self.agent, category="WEEKLY_AGENT_REMINDER").exists())

    def test_running_the_scheduler_twice_in_the_window_does_not_duplicate(self):
        zone = ZoneInfo("Europe/Istanbul")
        sunday = date(2026, 9, 6)
        reminder_time = datetime.combine(sunday, time(16, 0), tzinfo=zone)
        self._run(reminder_time)
        self._run(reminder_time)
        self.assertEqual(Notification.objects.filter(recipient=self.agent, category="WEEKLY_AGENT_REMINDER").count(), 1)

    def test_disabling_the_category_stops_the_reminder(self):
        NotificationSettings.objects.filter(pk=1).update(enable_weekly_agent_reminder=False)
        zone = ZoneInfo("Europe/Istanbul")
        closure_at = datetime.combine(date(2026, 9, 6), time(18, 0), tzinfo=zone)
        self.assertIsNone(send_weekly_agent_reminder(agent=self.agent, closure_at=closure_at))
        self.assertFalse(Notification.objects.filter(recipient=self.agent, category="WEEKLY_AGENT_REMINDER").exists())


@override_settings(MFA_ENABLED=False, LOCAL_AUTH_BYPASS=False,
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class WelcomeEmailAndActivationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@notif.test", password="test-password")
        self.client.force_login(self.admin)

    def _create_agent(self, email="new-agent@notif.test"):
        data = {"email": email, "first_name": "New", "last_name": "Agent",
                "password1": "Long-unique-password-2026", "password2": "Long-unique-password-2026",
                "monthly_salary_usd": "50"}
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("accounts:manage_create", args=[Role.AGENT]), data)
        self.assertEqual(response.status_code, 302)
        return User.objects.get(email=email)

    def test_account_creation_queues_a_welcome_email_with_activation_link_not_the_password(self):
        user = self._create_agent()
        welcome = Notification.objects.get(recipient=user, category="WELCOME")
        self.assertNotIn("Long-unique-password-2026", welcome.body)
        self.assertNotIn("Long-unique-password-2026", welcome.html_body)
        token = AccountActivationToken.objects.get(user=user)
        self.assertIn(str(user.pk), welcome.cta_url)
        self.assertTrue(NotificationDelivery.objects.filter(notification=welcome).exists())

    def test_activation_link_is_single_use(self):
        user = self._create_agent()
        _token, raw = issue_activation_token(user=user, actor=self.admin)
        activation = get_valid_activation_token(user_id=user.pk, raw_token=raw)
        self.assertIsNotNone(activation)
        consume_activation_token(token=activation, new_password="Brand-new-password-2026")
        self.assertIsNone(get_valid_activation_token(user_id=user.pk, raw_token=raw))
        user.refresh_from_db()
        self.assertTrue(user.check_password("Brand-new-password-2026"))

    def test_activation_link_expires(self):
        user = self._create_agent()
        token, raw = issue_activation_token(user=user, actor=self.admin)
        token.expires_at = timezone.now() - timedelta(hours=1)
        token.save(update_fields=["expires_at"])
        self.assertIsNone(get_valid_activation_token(user_id=user.pk, raw_token=raw))

    def test_resending_the_invitation_invalidates_the_previous_link(self):
        user = self._create_agent()
        _first_token, first_raw = issue_activation_token(user=user, actor=self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("accounts:manage_resend_invitation", args=[user.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(get_valid_activation_token(user_id=user.pk, raw_token=first_raw))
        self.assertEqual(AccountActivationToken.objects.filter(user=user, used_at__isnull=True, invalidated_at__isnull=True).count(), 1)

    def test_activation_view_rejects_invalid_token(self):
        user = self._create_agent()
        response = self.client.get(reverse("accounts:activate", kwargs={"user_id": user.pk, "token": "not-a-real-token"}))
        self.assertEqual(response.status_code, 302)

    def test_activation_view_sets_password_and_redirects_to_login(self):
        user = self._create_agent()
        _token, raw = issue_activation_token(user=user, actor=self.admin)
        url = reverse("accounts:activate", kwargs={"user_id": user.pk, "token": raw})
        response = self.client.post(url, {"new_password1": "Another-new-pass-2026", "new_password2": "Another-new-pass-2026"})
        self.assertRedirects(response, reverse("accounts:login"), fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertTrue(user.check_password("Another-new-pass-2026"))

    def test_disabling_welcome_email_category_skips_it(self):
        NotificationSettings.objects.update_or_create(pk=1, defaults={"enable_welcome_email": False})
        user = self._create_agent(email="no-welcome@notif.test")
        self.assertFalse(Notification.objects.filter(recipient=user, category="WELCOME").exists())


@override_settings(MFA_ENABLED=False, LOCAL_AUTH_BYPASS=False,
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class SalaryNotificationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="fin-admin@notif.test", password="test-password")
        self.agent = User.objects.create_user(email="paid-agent@notif.test", role=Role.AGENT)
        self.currency, _ = Currency.objects.get_or_create(code="USD")
        self.expense = Expense.objects.create(
            label="Salaire", category=ExpenseCategory.SALARY, agent=self.agent, amount=Decimal("50.00"),
            currency=self.currency, incurred_on=date(2026, 9, 30), created_by=self.admin,
        )

    def test_approving_a_salary_expense_notifies_the_agent_with_due_status(self):
        with self.captureOnCommitCallbacks(execute=True):
            decide_expense(expense_id=self.expense.pk, actor=self.admin, decision="APPROVED")
        notification = Notification.objects.get(recipient=self.agent, category="SALARY_INFO")
        self.assertIn("Validé", notification.body + notification.html_body if notification.html_body else notification.body)
        self.assertEqual(Notification.objects.filter(recipient=self.agent, category="SALARY_INFO").count(), 1)

    def test_rejecting_an_expense_does_not_notify_salary_info(self):
        with self.captureOnCommitCallbacks(execute=True):
            decide_expense(expense_id=self.expense.pk, actor=self.admin, decision="REJECTED")
        self.assertFalse(Notification.objects.filter(recipient=self.agent, category="SALARY_INFO").exists())

    def test_disabling_salary_notifications_skips_the_due_notification(self):
        NotificationSettings.objects.update_or_create(pk=1, defaults={"enable_salary_notifications": False})
        with self.captureOnCommitCallbacks(execute=True):
            decide_expense(expense_id=self.expense.pk, actor=self.admin, decision="APPROVED")
        self.assertFalse(Notification.objects.filter(recipient=self.agent, category="SALARY_INFO").exists())

    def test_salary_paid_confirmation_is_distinct_from_the_due_notification(self):
        with self.captureOnCommitCallbacks(execute=True):
            decide_expense(expense_id=self.expense.pk, actor=self.admin, decision="APPROVED")

        class _FakePayment:
            """`send_salary_paid` only reads these attributes; avoids needing a
            real ledger JournalBatch (out of scope for this notification test)."""
            pk = 4242
            expense = self.expense
            amount = Decimal("50.00")
            paid_at = timezone.now()

        payment = _FakePayment()
        send_salary_paid(payment=payment)
        self.assertTrue(Notification.objects.filter(recipient=self.agent, category="SALARY_INFO").exists())
        paid = Notification.objects.get(recipient=self.agent, category="SALARY_PAID")
        self.assertIn(str(payment.pk), paid.event_key)
        # Calling it twice for the same payment must not duplicate the confirmation.
        send_salary_paid(payment=payment)
        self.assertEqual(Notification.objects.filter(recipient=self.agent, category="SALARY_PAID").count(), 1)
