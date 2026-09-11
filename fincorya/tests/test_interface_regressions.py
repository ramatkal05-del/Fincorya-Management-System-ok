"""Exercise rendered navigation and real form submissions on an isolated database."""
from collections import deque
from html.parser import HTMLParser
from urllib.parse import urlsplit

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.operations.models import Operation
from apps.pricing.models import TariffSchedule
from tests.test_finance_workflows import FinanceScenario


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href", "").startswith("/"):
            self.links.add(attrs["href"])


@override_settings(FINANCE_LEDGER_ENABLED=True, LOCAL_AUTH_BYPASS=False,
                   MFA_ENABLED=False, SECURE_SSL_REDIRECT=False)
class InterfaceRegressionTests(FinanceScenario, TestCase):
    def test_rendered_navigation_for_every_role(self):
        self.admin.is_staff = self.admin.is_superuser = True
        self.admin.save()
        self.operation()
        self.period()
        self.expense()
        users = [self.admin, self.manager, self.agent]
        for role, party in [(Role.PARTNER, self.partner), (Role.SHAREHOLDER, self.shareholders[0])]:
            user = User.objects.create_user(email=f"crawl-{role}@example.test", role=role)
            party.owner = user
            party.save()
            users.append(user)
        for user in users:
            self.client.force_login(user)
            queue, seen = deque(["/"]), set()
            while queue:
                url = queue.popleft()
                if url in seen or urlsplit(url).query or "/logout/" in url or "/delete/" in url:
                    continue
                seen.add(url)
                with self.subTest(role=user.role, url=url):
                    response = self.client.get(url, follow=True)
                    self.assertLess(response.status_code, 400)
                    if "text/html" in response.get("Content-Type", ""):
                        parser = Links()
                        parser.feed(response.content.decode())
                        queue.extend(parser.links - seen)
            print(f"Navigation {user.role}: {len(seen)} pages", flush=True)

    def test_tariff_inline_invalid_and_overlapping_rows_return_form_errors(self):
        self.admin.is_staff = self.admin.is_superuser = True
        self.admin.save()
        self.client.force_login(self.admin)
        for minimum in ["", "1"]:
            with self.subTest(minimum=minimum):
                data = {"name": "New automatic tariff", "currency": self.usd.pk, "is_published": "on",
                        "tiers-TOTAL_FORMS": "2", "tiers-INITIAL_FORMS": "0", "tiers-MIN_NUM_FORMS": "0", "tiers-MAX_NUM_FORMS": "1000",
                        "tiers-0-min_amount": minimum, "tiers-0-max_amount": "100", "tiers-0-fixed_fee": "2",
                        "tiers-1-min_amount": "50", "tiers-1-max_amount": "200", "tiers-1-fixed_fee": "3"}
                response = self.client.post(reverse("admin:pricing_tariffschedule_add"), data)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(TariffSchedule.objects.filter(name=data["name"]).exists())

    def test_operation_preview_and_save_select_tariff_automatically(self):
        self.client.force_login(self.agent)
        response = self.client.get(reverse("operations:preview"), {"account": self.legacy.pk, "amount": "1000"})
        self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse("operations:create"), {
            "type": "SENT_TRANSFER", "account": self.legacy.pk, "service": "AIRTEL_MONEY",
            "customer_identifier": "+243123456789", "customer_name": "Client test", "amount": "1000",
            "commission_owner_confirmed": "on", "idempotency_key": "interface-auto-tariff"}, follow=True)
        self.assertEqual(response.status_code, 200)
        operation = Operation.objects.get(customer_name="Client test")
        self.assertEqual(operation.fee, 100)
