from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.finance.funds import submit_request
from apps.finance.models import DistributionPolicy, InternalTransfer, StakeholderRequest
from apps.stakeholders.models import Stakeholder
from tests.test_finance_workflows import FinanceScenario


@override_settings(FINANCE_LEDGER_ENABLED=True, LOCAL_AUTH_BYPASS=False, MFA_ENABLED=False, SECURE_SSL_REDIRECT=False)
class FinanceScreenTests(FinanceScenario, TestCase):
    def test_admin_screens_and_workflows_render_and_post(self):
        self.client.force_login(self.admin)
        for section in ['contributions', 'transfers', 'requests', 'guarantees', 'policies']:
            self.assertEqual(self.client.get(reverse('finance:workspace', args=[section])).status_code, 200)
        for name in ['contribution_create', 'transfer_create', 'guarantee_form', 'policy_create']:
            self.assertEqual(self.client.get(reverse(f'finance:{name}')).status_code, 200)
        holder = Stakeholder.objects.create(name='Screen holder', type='SHAREHOLDER')
        response = self.client.post(reverse('finance:contribution_create'), {'client_key': 'screen-1', 'origin': 'SHAREHOLDER', 'stakeholder_id': holder.pk, 'amount': '250',
            'currency': self.usd.pk, 'received_on': '2026-07-10', 'account_id': self.service_account.pk})
        self.assertEqual(response.status_code, 302)
        response = self.client.post(reverse('finance:transfer_create'), {'client_key': 'screen-t', 'source_id': self.service_account.pk, 'destination_id': self.cash.pk, 'amount': '100', 'fee': '0'})
        self.assertEqual(response.status_code, 302)
        transfer = InternalTransfer.objects.get(client_key='screen-t')
        self.client.post(reverse('finance:transfer_action', args=[transfer.pk, 'confirm']))
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, 'CONFIRMED')
        response = self.client.post(reverse('finance:policy_create'), {'mode': 'EQUAL_SHARES', 'effective_from': '2026-09-01', 'notes': 'Nouvelle règle'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(DistributionPolicy.objects.filter(mode='EQUAL_SHARES').exists())
        self.assertEqual(self.client.get(reverse('reports:center')).status_code, 200)
        response = self.client.get(reverse('reports:center'), {'kind': 'TREASURY', 'preset': 'MONTH', 'anchor': '2026-07-15', 'format': 'HTML'})
        self.assertContains(response, 'Rapprochement par caisse et compte')
        self.assertContains(response, 'PROVISOIRE')

    def test_partner_space_and_request_decision_screen(self):
        partner_user = User.objects.create_user(email='partner-screen@test.local', role=Role.PARTNER)
        self.partner.owner = partner_user
        self.partner.save(update_fields=['owner'])
        self.operation(partner=self.partner)
        self.client.force_login(partner_user)
        response = self.client.get(reverse('finance:party_space'))
        self.assertContains(response, self.partner.name)
        self.assertContains(response, 'Garantie, commissions dues')
        response = self.client.post(reverse('finance:party_space'), {'kind': 'CONVERT_COMMISSION', 'amount': '60', 'currency': self.usd.pk})
        self.assertEqual(response.status_code, 302)
        request = StakeholderRequest.objects.get()
        self.assertEqual(self.client.get(reverse('finance:request_decide', args=[request.pk])).status_code, 403)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('finance:request_decide', args=[request.pk])), 'Décision')
        self.client.post(reverse('finance:request_decide', args=[request.pk]), {'action': 'decide', 'd-decision': 'APPROVED', 'd-comment': ''})
        self.client.post(reverse('finance:request_decide', args=[request.pk]), {'action': 'execute'})
        request.refresh_from_db()
        self.assertEqual(request.status, 'EXECUTED')
        self.client.force_login(self.agent)
        self.assertEqual(self.client.get(reverse('finance:party_space')).status_code, 403)
