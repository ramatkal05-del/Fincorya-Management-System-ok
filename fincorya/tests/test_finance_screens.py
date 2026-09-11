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
        self.client.post(reverse('finance:transfer_action', args=[transfer.pk, 'receive']))
        self.client.post(reverse('finance:transfer_action', args=[transfer.pk, 'confirm']))
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, 'CONFIRMED')
        shareholder_ids = [str(sh.pk) for sh in self.shareholders]
        response = self.client.post(reverse('finance:policy_create'), {'mode': 'EQUAL_SHARES', 'effective_from': '2026-09-01', 'shareholders': shareholder_ids, 'notes': 'Nouvelle règle'})
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

    def test_report_center_has_native_download_form_and_distinct_monthly_fields(self):
        from html.parser import HTMLParser

        class Elements(HTMLParser):
            def __init__(self):
                super().__init__()
                self.ids, self.forms = [], []

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if "id" in attrs:
                    self.ids.append(attrs["id"])
                if tag == "form":
                    self.forms.append(attrs)

        self.client.force_login(self.admin)
        response = self.client.get(reverse('reports:center'))
        self.assertContains(response, 'Rapport mensuel consolidé')
        self.assertContains(response, 'Votre rapport apparaîtra ici')
        parser = Elements()
        parser.feed(response.content.decode())
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        report_form = next(form for form in parser.forms if form.get('action') == reverse('reports:center'))
        self.assertNotIn('hx-get', report_form)

    def test_report_errors_visible_for_normal_and_htmx_requests(self):
        self.client.force_login(self.admin)
        data = {'kind': 'TREASURY', 'preset': 'CUSTOM', 'anchor': '2026-07-15', 'format': 'HTML'}
        for headers in [{}, {'HTTP_HX_REQUEST': 'true'}]:
            response = self.client.get(reverse('reports:center'), data, **headers)
            self.assertContains(response, 'Indiquez une date de fin')

    def test_htmx_export_requests_trigger_a_real_download(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('reports:center'), {
            'kind': 'TREASURY', 'preset': 'MONTH', 'anchor': '2026-07-15', 'format': 'CSV',
        }, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertIn('HX-Redirect', response)
        download = self.client.get(response['HX-Redirect'])
        self.assertEqual(download.status_code, 200)
        self.assertIn('attachment', download['Content-Disposition'])
        download.close()

    def test_custom_services_are_available_in_report_filters(self):
        from apps.operations.models import TransactionServiceOption
        from apps.reports.forms import FinanceReportForm
        TransactionServiceOption.objects.create(code='CUSTOM_WAVE', label='Wave historique', is_active=False)
        form = FinanceReportForm(user=self.admin)
        self.assertIn(('CUSTOM_WAVE', 'Wave historique'), form.fields['service'].choices)

    def test_personal_situation_keeps_polling_after_each_refresh(self):
        user = User.objects.create_user(email='polling-partner@test.local', role=Role.PARTNER)
        self.partner.owner = user
        self.partner.save(update_fields=['owner'])
        self.client.force_login(user)
        for headers in [{}, {'HTTP_HX_REQUEST': 'true'}, {'HTTP_HX_REQUEST': 'true'}]:
            response = self.client.get(reverse('finance:party_space'), **headers)
            self.assertContains(response, 'id="party-situation"', count=1)
            self.assertContains(response, 'hx-trigger="every 60s"', count=1)
            self.assertContains(response, 'hx-swap="outerHTML"', count=1)
            if headers:
                self.assertNotContains(response, 'Soumettre la demande')

    def test_monthly_form_errors_are_rendered_and_exports_have_readable_labels(self):
        from apps.reports.models import ReportExport
        self.client.force_login(self.admin)
        response = self.client.post(reverse('reports:monthly'), {'year': 'invalid', 'month': '7', 'format': 'CSV'})
        self.assertContains(response, 'monthly_year_errors')
        export = ReportExport.objects.create(requested_by=self.admin, kind='MONTHLY_FINANCIAL', format='CSV', status='READY')
        self.assertEqual(export.display_title, 'Rapport mensuel consolidé')
        self.assertEqual(export.display_status, 'Disponible')
