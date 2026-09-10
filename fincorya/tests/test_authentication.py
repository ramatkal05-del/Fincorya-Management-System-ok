from django.test import TestCase, override_settings
from django.urls import reverse
from django.core import mail
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import Role, User
from apps.accounts.services import regenerate_recovery_codes
from apps.audit.models import AuditEvent
from apps.pricing.models import Currency, TariffSchedule, TariffTier
from apps.cash.models import CashAccount
from apps.expenses.models import Expense
from decimal import Decimal
from datetime import date


@override_settings(SECURE_SSL_REDIRECT=False, MFA_ENABLED=True, LOCAL_AUTH_BYPASS=False)
class AuthenticationFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="agent.ui@fincorya.test", password="Strong-pass-123", role=Role.AGENT)

    def test_anonymous_dashboard_redirects_to_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, f"{reverse('accounts:login')}?next={reverse('dashboard')}")

    def test_login_page_exposes_responsive_htmx_states_without_duplicate_security_label(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="auth-page login-page"')
        self.assertContains(response, 'autocomplete="email"')
        self.assertContains(response, 'autocomplete="current-password"')
        self.assertContains(response, 'hx-boost="true"')
        self.assertContains(response, 'class="login-spinner"')
        self.assertContains(response, "Système de Gestion Interne")
        self.assertNotContains(response, "Espace sécurisé")
        self.assertNotContains(response, "auth-watermark")

    def test_first_login_requires_totp_setup(self):
        response = self.client.post(reverse("accounts:login"), {"email": self.user.email, "password": "Strong-pass-123"})
        self.assertRedirects(response, reverse("accounts:totp_setup"))
        setup = self.client.get(reverse("accounts:totp_setup"))
        self.assertEqual(setup.status_code, 200)
        self.assertContains(setup, "Activez la double authentification")
        self.assertContains(setup, "data:image/png;base64")

    def test_totp_user_is_sent_to_second_step(self):
        self.user.totp_enabled = True
        self.user.save(update_fields=["totp_enabled"])
        TOTPDevice.objects.create(user=self.user, name="FINCORYA", confirmed=True)
        response = self.client.post(reverse("accounts:login"), {"email": self.user.email, "password": "Strong-pass-123"})
        self.assertRedirects(response, reverse("accounts:verify"))
        self.assertEqual(self.client.session["preauth_user_id"], self.user.pk)
        verify = self.client.get(reverse("accounts:verify"))
        self.assertContains(verify, "QR + code TOTP")
        self.assertContains(verify, "QR d’activation")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_user_can_request_and_validate_email_mfa_code(self):
        self.user.totp_enabled = True
        self.user.save(update_fields=["totp_enabled"])
        TOTPDevice.objects.create(user=self.user, name="FINCORYA", confirmed=True)
        login_response = self.client.post(reverse("accounts:login"), {
            "email": self.user.email, "password": "Strong-pass-123",
        })
        self.assertRedirects(login_response, reverse("accounts:verify"))
        send_response = self.client.post(reverse("accounts:send_email_otp"))
        self.assertRedirects(send_response, f"{reverse('accounts:verify')}?method=email")
        self.assertEqual(len(mail.outbox), 1)
        code = next(part for part in mail.outbox[0].body.split() if part.isdigit() and len(part) == 6)
        verify_response = self.client.post(reverse("accounts:verify"), {"method": "email", "token": code})
        self.assertRedirects(verify_response, reverse("dashboard"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)
        self.assertTrue(AuditEvent.objects.filter(actor=self.user, action="MFA_EMAIL_SENT").exists())
        self.assertTrue(AuditEvent.objects.filter(actor=self.user, action="MFA_VERIFIED", after__method="email").exists())

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_email_mfa_send_is_rate_limited(self):
        self.user.totp_enabled = True
        self.user.save(update_fields=["totp_enabled"])
        TOTPDevice.objects.create(user=self.user, name="FINCORYA", confirmed=True)
        self.client.post(reverse("accounts:login"), {"email": self.user.email, "password": "Strong-pass-123"})
        for _ in range(4):
            self.client.post(reverse("accounts:send_email_otp"))
        self.assertEqual(len(mail.outbox), 3)

    def test_invalid_password_does_not_reveal_account(self):
        response = self.client.post(reverse("accounts:login"), {"email": self.user.email, "password": "wrong"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Adresse e-mail ou mot de passe incorrect")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_recovery_codes_are_hashed_and_single_use_capable(self):
        raw_codes = regenerate_recovery_codes(self.user, count=2)
        stored = self.user.recovery_codes.first()
        self.assertNotIn(raw_codes[0], [code.code_hash for code in self.user.recovery_codes.all()])
        self.assertTrue(any(code.matches(raw) for code in self.user.recovery_codes.all() for raw in raw_codes))
        self.assertTrue(stored.code_hash.startswith("pbkdf2_"))

    def test_verified_session_renders_enterprise_dashboard(self):
        self.user.totp_enabled = True
        self.user.save(update_fields=["totp_enabled"])
        self.client.force_login(self.user)
        session = self.client.session
        session["fincorya_recovery_verified"] = True
        session.save()
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vue d'ensemble")
        self.assertContains(response, "fincorya-group-logo-fr.svg")
        self.assertContains(response, "Environnement sécurisé")

    def test_login_is_rate_limited_after_repeated_failures(self):
        payload = {"email": self.user.email, "password": "incorrect"}
        for _ in range(5):
            self.client.post(reverse("accounts:login"), payload)
        blocked = self.client.post(reverse("accounts:login"), payload)
        self.assertEqual(blocked.status_code, 429)
        self.assertContains(blocked, "Trop de tentatives", status_code=429)

    @override_settings(MFA_ENABLED=False)
    def test_login_bypasses_totp_when_mfa_is_disabled(self):
        response = self.client.post(reverse("accounts:login"), {
            "email": self.user.email, "password": "Strong-pass-123",
        })
        self.assertRedirects(response, reverse("dashboard"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)


@override_settings(SECURE_SSL_REDIRECT=False, LOCAL_AUTH_BYPASS=False)
class ProtectedWorkflowViewTests(TestCase):
    def verified_login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["fincorya_recovery_verified"] = True
        session.save()

    def setUp(self):
        self.agent = User.objects.create_user(email="agent.views@fincorya.test", password="test", role=Role.AGENT)
        self.admin = User.objects.create_user(email="admin.views@fincorya.test", password="test", role=Role.ADMIN)
        self.other_admin = User.objects.create_user(email="other-admin.views@fincorya.test", password="test", role=Role.ADMIN)
        self.usd = Currency.objects.create(code="USD")
        self.agent_account = CashAccount.objects.create(agent=self.agent, currency=self.usd, is_active=True, allocated_by=self.admin)
        self.schedule = TariffSchedule.objects.create(name="Transferts test", currency=self.usd, is_published=True)
        TariffTier.objects.create(schedule=self.schedule, min_amount=Decimal("1.00"), max_amount=Decimal("100.00"), fixed_fee=Decimal("8.00"))

    def test_operation_preview_calculates_usd_commission_without_exchange_rate_row(self):
        self.verified_login(self.agent)
        response = self.client.get(reverse("operations:preview"), {
            "tariff_schedule": self.schedule.pk,
            "type": "SENT_TRANSFER",
            "account": self.agent_account.pk,
            "service": "VODACOM_MPESA",
            "customer_identifier": "+243555555",
            "customer_name": "Rauf Ramat",
            "amount": "50.00",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "8,00")
        self.assertContains(response, "USD")

    def test_agent_cannot_open_expense_register(self):
        self.verified_login(self.agent)
        self.assertEqual(self.client.get(reverse("expenses:list")).status_code, 403)

    def test_accountant_can_create_and_view_expense(self):
        self.verified_login(self.admin)
        response = self.client.post(reverse("expenses:create"), {
            "category": "GENERAL", "label": "Connexion", "amount": "25.00", "currency": self.usd.pk,
            "incurred_on": date.today().isoformat(),
        })
        expense = Expense.objects.get()
        self.assertRedirects(response, reverse("expenses:detail", args=[expense.pk]))
        self.assertEqual(expense.status, "PENDING")
        self.assertEqual(self.client.get(reverse("expenses:detail", args=[expense.pk])).status_code, 200)
        decision = self.client.post(reverse("expenses:decide", args=[expense.pk]), {"decision": "APPROVED", "comment": "Vérifié"})
        self.assertRedirects(decision, reverse("expenses:detail", args=[expense.pk]))
        expense.refresh_from_db()
        self.assertEqual(expense.status, "APPROVED")

    def test_monthly_report_form_accepts_stakeholder_type_filter(self):
        self.verified_login(self.admin)
        response = self.client.post(reverse("reports:monthly"), {
            "year": date.today().year,
            "month": date.today().month, "agent": "", "stakeholder": "",
            "stakeholder_type": "", "format": "CSV",
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn("/rapports/", response["Location"])

    def test_security_headers_are_present(self):
        self.verified_login(self.admin)
        response = self.client.get(reverse("dashboard"))
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["Permissions-Policy"], "camera=(), microphone=(), geolocation=()")

    def test_report_download_is_private_to_requester(self):
        self.verified_login(self.admin)
        response = self.client.get(reverse("reports:center"), {
            "kind": "ACTIVITY", "preset": "DAY", "anchor": date.today().isoformat(), "format": "CSV",
        })
        location = response["Location"]
        self.client.logout()
        self.verified_login(self.other_admin)
        self.assertEqual(self.client.get(location).status_code, 404)

    def test_paypal_operation_requires_email_identifier(self):
        self.verified_login(self.agent)
        response = self.client.post(reverse("operations:create"), {
            "type": "SENT_TRANSFER", "account": self.agent_account.pk,
            "service": "PAYPAL", "customer_identifier": "pas-un-email", "customer_name": "Client Test",
            "amount": "25.00", "idempotency_key": "paypal-invalid-email",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "adresse e-mail valide")

    def test_profile_is_available_to_every_user_role(self):
        for index, role in enumerate(Role.values):
            with self.subTest(role=role):
                user = User.objects.create_user(
                    email=f"profile-{index}@fincorya.test", password="test", role=role,
                )
                self.verified_login(user)
                response = self.client.get(reverse("accounts:profile"))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Mon profil")
                self.client.logout()

    def test_user_can_update_only_own_profile_and_change_email_login(self):
        self.verified_login(self.agent)
        response = self.client.post(reverse("accounts:profile"), {
            "first_name": "Aline",
            "last_name": "Kabongo",
            "email": "aline.kabongo@fincorya.test",
            "phone": "+243 810 000 001",
            "city": "Kinshasa",
            "language": "fr",
        })
        self.assertRedirects(response, reverse("accounts:profile"))
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.get_full_name(), "Aline Kabongo")
        self.assertEqual(self.agent.email, "aline.kabongo@fincorya.test")
        self.assertEqual(self.agent.username, "aline.kabongo@fincorya.test")
        self.assertEqual(self.agent.role, Role.AGENT)
        self.assertTrue(AuditEvent.objects.filter(actor=self.agent, action="PROFILE_UPDATE").exists())

    def test_duplicate_profile_email_is_rejected(self):
        self.verified_login(self.agent)
        response = self.client.post(reverse("accounts:profile"), {
            "first_name": "Agent",
            "last_name": "Test",
            "email": self.admin.email.upper(),
            "phone": "",
            "city": "",
            "language": "fr",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cette adresse e-mail est déjà utilisée")
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.email, "agent.views@fincorya.test")

    def test_user_can_change_password_without_losing_current_session(self):
        self.verified_login(self.agent)
        response = self.client.post(reverse("accounts:password_change"), {
            "old_password": "test",
            "new_password1": "Nouveau-mot-de-passe-2026!",
            "new_password2": "Nouveau-mot-de-passe-2026!",
        })
        self.assertRedirects(response, reverse("accounts:profile"))
        self.agent.refresh_from_db()
        self.assertTrue(self.agent.check_password("Nouveau-mot-de-passe-2026!"))
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)
        self.assertTrue(AuditEvent.objects.filter(actor=self.agent, action="PASSWORD_CHANGE").exists())

    def test_password_change_requires_current_password(self):
        self.verified_login(self.agent)
        response = self.client.post(reverse("accounts:password_change"), {
            "old_password": "incorrect",
            "new_password1": "Nouveau-mot-de-passe-2026!",
            "new_password2": "Nouveau-mot-de-passe-2026!",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ancien mot de passe est incorrect")
        self.agent.refresh_from_db()
        self.assertTrue(self.agent.check_password("test"))
