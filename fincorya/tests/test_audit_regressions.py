"""Regression coverage for authentication and malformed request failures."""
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.management.commands.create_admin import ADMIN_EMAIL
from apps.accounts.models import RecoveryCode, Role, User
from apps.accounts.services import regenerate_recovery_codes
from apps.cash.models import CashAccount
from apps.pricing.models import Currency, TariffSchedule
from apps.reports.models import ReportExport


@override_settings(LOCAL_AUTH_BYPASS=False, SECURE_SSL_REDIRECT=False, MFA_ENABLED=True)
class AdminMFARegressionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(email="audit-admin@example.test", password="Test-password-843!")

    def test_admin_login_cannot_bypass_application_mfa(self):
        response = self.client.post(reverse("admin:login"), {
            "username": self.admin.email, "password": "Test-password-843!", "next": "/admin/",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("accounts:login")))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_password_only_session_cannot_access_admin(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("admin:index"))
        self.assertRedirects(response, reverse("accounts:totp_setup"), fetch_redirect_response=False)

    def test_verified_admin_remains_allowed(self):
        self.client.force_login(self.admin)
        session = self.client.session
        session["fincorya_mfa_verified"] = True
        session.save()
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)

    def test_admin_csrf_protection_is_preserved(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        session = client.session
        session["fincorya_mfa_verified"] = True
        session.save()
        response = client.post(reverse("admin:accounts_user_changelist"), {"action": "delete_selected"})
        self.assertEqual(response.status_code, 403)

    @override_settings(MFA_ENABLED=False)
    def test_explicitly_disabled_mfa_preserves_admin_access(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)


@override_settings(LOCAL_AUTH_BYPASS=False, SECURE_SSL_REDIRECT=False, MFA_ENABLED=True)
class RecoveryRegressionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="audit-recovery@example.test", password="test", totp_enabled=True)
        self.raw = regenerate_recovery_codes(self.user, count=1)[0]
        session = self.client.session
        session["preauth_user_id"] = self.user.pk
        session.save()

    def test_concurrently_consumed_code_cannot_authenticate(self):
        original = RecoveryCode.matches

        def consume_elsewhere(code, raw):
            matched = original(code, raw)
            RecoveryCode.objects.filter(pk=code.pk).update(used_at=timezone.now())
            return matched

        with patch.object(RecoveryCode, "matches", consume_elsewhere):
            response = self.client.post(reverse("accounts:verify"), {"method": "recovery", "token": self.raw})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_valid_recovery_code_is_consumed(self):
        response = self.client.post(reverse("accounts:verify"), {"method": "recovery", "token": self.raw})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)
        self.assertIsNotNone(self.user.recovery_codes.get().used_at)

    def test_verification_page_is_not_cached(self):
        response = self.client.get(reverse("accounts:verify"))
        self.assertIn("no-store", response.get("Cache-Control", ""))


@override_settings(LOCAL_AUTH_BYPASS=False, SECURE_SSL_REDIRECT=False, MFA_ENABLED=False)
class RequestRegressionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email="audit-agent@example.test", role=Role.AGENT)
        cls.currency = Currency.objects.create(code="USD")
        cls.account = CashAccount.objects.create(agent=cls.user, currency=cls.currency, is_active=True)
        cls.schedule = TariffSchedule.objects.create(name="Audit tariff", currency=cls.currency, is_published=True)

    def setUp(self):
        self.client.force_login(self.user)

    def test_monthly_report_is_revoked_after_admin_role_is_removed(self):
        export = ReportExport.objects.create(requested_by=self.user, kind="MONTHLY_FINANCIAL", format="CSV", status="READY")
        response = self.client.get(reverse("reports:download", args=[export.public_id]))
        self.assertEqual(response.status_code, 403)

    def test_non_finite_operation_amounts_are_rejected(self):
        for amount in ["NaN", "sNaN", "Infinity", "-Infinity", "1e999999"]:
            with self.subTest(amount=amount):
                response = self.client.get(reverse("operations:preview"), {
                    "account": self.account.pk, "tariff_schedule": self.schedule.pk, "amount": amount,
                })
                self.assertEqual(response.status_code, 422)

    def test_non_finite_cash_counts_do_not_crash_preview(self):
        for amount in ["NaN", "sNaN", "Infinity", "-Infinity", "1e999999"]:
            with self.subTest(amount=amount):
                response = self.client.get(reverse("cash:closure_preview", args=[self.account.pk]), {"declared_cash": amount})
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.context["declared"])


class InitialAdminRegressionTests(TestCase):
    @override_settings(ADMIN_PASSWORD="")
    def test_missing_password_does_not_create_predictable_admin(self):
        with self.assertRaises(CommandError):
            call_command("create_admin", stdout=StringIO())
        self.assertFalse(User.objects.filter(email=ADMIN_EMAIL).exists())

    @override_settings(ADMIN_PASSWORD="Configured-password-348!")
    def test_initial_admin_uses_configured_password(self):
        call_command("create_admin", stdout=StringIO())
        self.assertTrue(User.objects.get(email=ADMIN_EMAIL).check_password("Configured-password-348!"))

    @override_settings(ADMIN_PASSWORD="")
    def test_existing_admin_is_not_changed(self):
        user = User.objects.create_superuser(email=ADMIN_EMAIL, password="Existing-password-935!")
        call_command("create_admin", stdout=StringIO())
        user.refresh_from_db()
        self.assertTrue(user.check_password("Existing-password-935!"))
