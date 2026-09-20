from django.test import TestCase, override_settings
from django.urls import reverse
from apps.accounts.models import Role, User
from apps.accounts.management_views import ManagedAccountForm
from apps.stakeholders.models import Stakeholder
from apps.finance.forms import PartyForm


@override_settings(MFA_ENABLED=False, LOCAL_AUTH_BYPASS=False,
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AccountManagementTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@management.test", password="test-password")
        self.client.force_login(self.admin)

    def data(self, email="agent@management.test"):
        return {"email": email, "first_name": "Agent", "last_name": "Test",
                "password1": "Long-unique-password-2026", "password2": "Long-unique-password-2026",
                "monthly_salary_usd": "50"}

    def test_admin_creates_agent_with_password_and_without_economic_side_effects(self):
        response = self.client.post(reverse("accounts:manage_create", args=[Role.AGENT]), self.data())
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email="agent@management.test")
        self.assertTrue(user.check_password("Long-unique-password-2026"))
        self.assertEqual(user.role, Role.AGENT)
        self.assertFalse(user.is_staff)
        self.assertFalse(Stakeholder.objects.exists())

    def test_each_role_has_separate_fields_and_cannot_be_overridden_by_post(self):
        for role in [Role.PARTNER, Role.INVESTOR, Role.SHAREHOLDER, Role.FINANCE_MANAGER]:
            form = ManagedAccountForm(role=role)
            self.assertNotIn("monthly_salary_usd", form.fields)
            self.assertNotIn("investment_amount", form.fields)
            data = {**self.data(f"{role.lower()}@management.test"), "role": Role.ADMIN, "is_superuser": "on"}
            response = self.client.post(reverse("accounts:manage_create", args=[role]), data)
            self.assertEqual(response.status_code, 302)
            user = User.objects.get(email=data["email"])
            self.assertEqual(user.role, role)
            self.assertFalse(user.is_superuser)

    def test_non_admin_cannot_create_or_list_accounts(self):
        for role in [Role.AGENT, Role.FINANCE_MANAGER, Role.PARTNER]:
            user = User.objects.create_user(email=f"blocked-{role}@management.test", role=role)
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("accounts:manage")).status_code, 403)
            self.assertEqual(self.client.post(reverse("accounts:manage_create", args=[Role.AGENT]), self.data()).status_code, 403)

    def test_admin_form_includes_passwords_and_no_contract_fields(self):
        response = self.client.get(reverse("admin:accounts_user_add"))
        self.assertContains(response, 'name="password1"')
        self.assertContains(response, 'name="password2"')
        self.assertNotContains(response, 'name="contract_title"')
        self.assertContains(response, "admin-fincorya.css")

    def test_email_duplicate_is_reported_on_form(self):
        User.objects.create_user(email="existing@management.test", role=Role.AGENT)
        form = ManagedAccountForm(self.data("EXISTING@management.test"), role=Role.AGENT)
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_django_admin_creates_a_usable_account_without_contract(self):
        data = {**self.data("admin-created@management.test"), "role": Role.AGENT, "_save": "Enregistrer"}
        response = self.client.post(reverse("admin:accounts_user_add"), data)
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email=data["email"])
        self.assertTrue(user.check_password(data["password1"]))
        self.assertFalse(Stakeholder.objects.exists())

    def test_economic_dossier_does_not_change_login_role(self):
        agent = User.objects.create_user(email="agent-party@management.test", role=Role.AGENT)
        form = PartyForm({"name": "Agent", "type": "INVESTOR", "owner": agent.pk})
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        agent.refresh_from_db()
        self.assertEqual(agent.role, Role.AGENT)
        self.assertFalse(agent.is_staff)

    def test_stale_photo_reference_self_heals_instead_of_repeated_404s(self):
        from django.core.files.base import ContentFile
        self.admin.photo.save("missing.jpg", ContentFile(b"data"), save=True)
        # Simulate a media reset: the DB still references a file that is gone from disk.
        self.admin.photo.storage.delete(self.admin.photo.name)
        response = self.client.get(reverse("accounts:profile_photo", args=[self.admin.pk]))
        self.assertEqual(response.status_code, 404)
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.photo)

    def test_own_administrator_cannot_be_disabled(self):
        response = self.client.post(reverse("accounts:manage_status", args=[self.admin.pk]))
        self.assertEqual(response.status_code, 403)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
