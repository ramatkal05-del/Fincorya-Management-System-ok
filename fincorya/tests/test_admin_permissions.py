from django.contrib.auth.models import Group, Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.audit.models import AuditEvent
from apps.cash.models import CashAccount, CashMovement, GlobalCashAccount, GlobalCashMovement
from apps.pricing.models import Currency


@override_settings(MFA_ENABLED=False, LOCAL_AUTH_BYPASS=False, SECURE_SSL_REDIRECT=False,
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AdminPermissionTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(email="root@permissions.test", password="test")
        self.admin = User.objects.create_user(email="admin@permissions.test", role=Role.ADMIN)
        self.user = User.objects.create_user(email="agent@permissions.test", role=Role.AGENT)
        self.client.force_login(self.superuser)

    def test_superuser_can_assign_groups_and_permissions_on_creation(self):
        group = Group.objects.create(name="Consultation des devises")
        permission = Permission.objects.get(content_type__app_label="pricing", codename="view_currency")
        group.permissions.add(permission)
        response = self.client.post(reverse("admin:accounts_user_add"), {
            "email": "delegated@permissions.test", "role": Role.FINANCE_MANAGER,
            "password1": "Long-unique-password-2026", "password2": "Long-unique-password-2026",
            "is_staff": "on", "groups": [group.pk], "user_permissions": [permission.pk],
        })
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email="delegated@permissions.test")
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(list(user.groups.all()), [group])
        self.assertTrue(user.has_perm("pricing.view_currency"))
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("admin:pricing_currency_changelist")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:pricing_currency_add")).status_code, 403)

    def test_regular_admin_cannot_promote_accounts_or_edit_superusers(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("admin:accounts_user_add"))
        for field in ("groups", "user_permissions", "is_superuser", "is_staff"):
            self.assertNotContains(response, f'name="{field}"')
        response = self.client.post(reverse("admin:accounts_user_add"), {
            "email": "forged@permissions.test", "role": Role.AGENT,
            "password1": "Long-unique-password-2026", "password2": "Long-unique-password-2026",
            "is_staff": "on", "is_superuser": "on",
        })
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email="forged@permissions.test")
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)
        response = self.client.post(reverse("admin:accounts_user_change", args=[self.superuser.pk]), {})
        self.assertEqual(response.status_code, 403)

    def test_superuser_with_another_business_role_keeps_system_access(self):
        self.superuser.role = Role.FINANCE_MANAGER
        self.superuser.save(update_fields=["role"])
        self.assertEqual(self.client.get(reverse("admin:accounts_user_add")).status_code, 200)

    def test_account_without_financial_history_can_be_deleted(self):
        response = self.client.post(reverse("admin:accounts_user_delete", args=[self.user.pk]), {"post": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())

    def test_deactivation_keeps_cash_history_and_skips_own_account(self):
        currency = Currency.objects.create(code="USD")
        account = CashAccount.objects.create(agent=self.user, currency=currency)
        movement = CashMovement.objects.create(account=account, direction="IN", movement_type="OPENING", amount=10, balance_after=10)
        response = self.client.get(reverse("admin:accounts_user_delete", args=[self.user.pk]))
        self.assertContains(response, "Conserver l’historique financier")
        self.client.post(reverse("admin:accounts_user_changelist"), {
            "action": "deactivate_selected", "_selected_action": [self.user.pk, self.superuser.pk],
        })
        self.user.refresh_from_db()
        self.superuser.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertTrue(self.superuser.is_active)
        self.assertTrue(CashMovement.objects.filter(pk=movement.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(action="ADMIN_DEACTIVATE", object_id=str(self.user.pk)).exists())

    def test_global_cash_can_be_deactivated_without_deleting_movements(self):
        currency = Currency.objects.create(code="USD")
        account = GlobalCashAccount.objects.create(administrator=self.superuser, currency=currency)
        movement = GlobalCashMovement.objects.create(global_account=account, direction="IN", movement_type="CAPITAL_IN",
                                                     amount=10, balance_after=10, created_by=self.superuser, note="Test")
        response = self.client.post(reverse("admin:cash_globalcashaccount_changelist"), {
            "action": "deactivate_selected", "_selected_action": [account.pk],
        })
        self.assertEqual(response.status_code, 302)
        account.refresh_from_db()
        self.assertFalse(account.is_active)
        self.assertTrue(GlobalCashMovement.objects.filter(pk=movement.pk).exists())
        response = self.client.post(reverse("admin:cash_globalcashmovement_delete", args=[movement.pk]), {"post": "yes"})
        self.assertEqual(response.status_code, 403)

    def test_delegated_change_permission_cannot_promote_business_role(self):
        delegated = User.objects.create_user(email="staff@permissions.test", role=Role.AGENT, is_staff=True)
        delegated.user_permissions.add(Permission.objects.get(content_type__app_label="accounts", codename="change_user"))
        self.client.force_login(delegated)
        response = self.client.get(reverse("admin:accounts_user_change", args=[self.user.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="role"')
        response = self.client.post(reverse("admin:accounts_user_change", args=[self.admin.pk]), {})
        self.assertEqual(response.status_code, 403)
