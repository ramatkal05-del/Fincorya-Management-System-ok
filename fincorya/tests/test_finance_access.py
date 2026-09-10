"""Server-side access rules for reports, exports and party spaces."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.finance.closing import approve_distributions, close_period, propose_distribution
from apps.finance.models import DistributionPolicy
from apps.finance.reporting import allowed_kinds, build_report, period_bounds, safe_cell
from apps.profits.models import ProfitPeriod
from apps.reports.models import ReportExport
from apps.reports.services import _text, generate_finance_report
from apps.stakeholders.models import Stakeholder
from tests.test_finance_workflows import AUGUST, JULY, FinanceScenario


@override_settings(FINANCE_LEDGER_ENABLED=True, LOCAL_AUTH_BYPASS=False, MFA_ENABLED=False, SECURE_SSL_REDIRECT=False)
class ReportAccessTests(FinanceScenario, TestCase):
    def setUp(self):
        super().setUp()
        self.other_agent = User.objects.create_user(email='other-agent@test.local', role=Role.AGENT)
        self.holder_user = User.objects.create_user(email='holder@test.local', role=Role.SHAREHOLDER)
        self.investor_user = User.objects.create_user(email='investor@test.local', role=Role.INVESTOR)
        self.partner_user = User.objects.create_user(email='partner@test.local', role=Role.PARTNER)
        self.holder = Stakeholder.objects.create(name='Holder', type='SHAREHOLDER', owner=self.holder_user)
        self.investor = Stakeholder.objects.create(name='Investor', type='INVESTOR', owner=self.investor_user)
        self.partner.owner = self.partner_user
        self.partner.save(update_fields=['owner'])
        self.operation()

    def test_agent_only_sees_own_operations_in_daily_report(self):
        mine = build_report(user=self.agent, kind='ACTIVITY', preset='DAY', anchor=date(2026, 7, 15))
        detail = next(s for s in mine['sections'] if s['title'] == 'Détail des opérations')
        self.assertEqual(len(detail['rows']), 1)
        self.assertEqual(detail['rows'][0][5], self.agent.email)
        self.assertEqual(build_report(user=self.other_agent, kind='ACTIVITY', preset='DAY', anchor=date(2026, 7, 15))['sections'][-1]['rows'], [])
        with self.assertRaises(PermissionDenied):
            build_report(user=self.agent, kind='ACTIVITY', preset='MONTH', anchor=date(2026, 7, 15))
        with self.assertRaises(PermissionDenied):
            build_report(user=self.agent, kind='TREASURY', preset='DAY', anchor=date(2026, 7, 15))

    def test_agent_cannot_export_another_agent_report_via_url(self):
        self.client.force_login(self.agent)
        response = self.client.get(reverse('reports:center'), {'kind': 'ACTIVITY', 'preset': 'DAY', 'anchor': '2026-07-15', 'format': 'CSV', 'agent': self.other_agent.pk})
        export = ReportExport.objects.get(requested_by=self.agent)
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('agent', export.filters)
        self.assertEqual(export.snapshot['sections'][-1]['rows'][0][5], self.agent.email)
        response = self.client.get(reverse('reports:center'), {'kind': 'TREASURY', 'preset': 'DAY', 'anchor': '2026-07-15', 'format': 'CSV'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ReportExport.objects.filter(requested_by=self.agent).count(), 1)

    def test_party_roles_see_only_their_own_situation(self):
        self.assertEqual(set(allowed_kinds(self.investor_user)), {'INVESTORS'})
        self.assertEqual(set(allowed_kinds(self.partner_user)), {'PARTNERS'})
        self.assertEqual(set(allowed_kinds(self.holder_user)), {'SHAREHOLDERS', 'ACTIVITY', 'RESULT'})
        partners = build_report(user=self.partner_user, kind='PARTNERS', preset='MONTH', anchor=date(2026, 7, 15))
        self.assertEqual(partners['filters'], {'party': self.partner.name})
        self.assertTrue(all(row[0] == self.partner.name for row in partners['sections'][0]['rows']))
        for user, kind in ((self.investor_user, 'PARTNERS'), (self.partner_user, 'INVESTORS'), (self.investor_user, 'ACTIVITY')):
            with self.assertRaises(PermissionDenied):
                build_report(user=user, kind=kind, preset='MONTH', anchor=date(2026, 7, 15))
        with self.assertRaises(PermissionDenied):
            generate_finance_report(user=self.holder_user, kind='SHAREHOLDERS', preset='MONTH', anchor=date(2026, 7, 15), format='PDF')
        with override_settings(REPORT_DOWNLOAD_ROLES=['ADMIN', 'SHAREHOLDER']):
            export = generate_finance_report(user=self.holder_user, kind='SHAREHOLDERS', preset='MONTH', anchor=date(2026, 7, 15), format='PDF')
        self.assertEqual(export.status, 'READY')

    def test_shareholder_reads_global_activity_but_cannot_modify(self):
        self.client.force_login(self.holder_user)
        self.assertEqual(self.client.get(reverse('finance:party_space')).status_code, 200)
        self.assertEqual(self.client.get(reverse('finance:overview')).status_code, 403)
        self.assertEqual(self.client.post(reverse('finance:batch_create')).status_code, 403)
        self.assertEqual(self.client.post(reverse('finance:contribution_create')).status_code, 403)
        self.assertEqual(self.client.get(reverse('operations:create')).status_code, 403)
        self.assertContains(self.client.get(reverse('operations:list')), str(self.legacy.operations.first().reference))
        report = build_report(user=self.holder_user, kind='ACTIVITY', preset='MONTH', anchor=date(2026, 7, 15))
        self.assertEqual(len(report['sections'][-1]['rows']), 1)

    def test_finance_manager_gets_weekly_global_report_but_not_personal_files(self):
        kinds = set(allowed_kinds(self.manager))
        self.assertIn('TREASURY', kinds)
        self.assertNotIn('INVESTORS', kinds)
        start, end, label = period_bounds('WEEK', date(2026, 7, 15))
        self.assertEqual((start, end), (date(2026, 7, 13), date(2026, 7, 19)))
        self.assertIn('lundi', label)
        with override_settings(FINANCE_WEEK_START=6):
            start, end, _ = period_bounds('WEEK', date(2026, 7, 15))
            self.assertEqual((start, end), (date(2026, 7, 12), date(2026, 7, 18)))
        report = build_report(user=self.manager, kind='TREASURY', preset='WEEK', anchor=date(2026, 7, 15))
        self.assertEqual(report['status'], 'PROVISOIRE')

    def test_exports_and_screen_share_the_same_totals_and_escape_formulas(self):
        policy = DistributionPolicy.objects.create(mode='EQUAL_SHARES', effective_from=date(2026, 7, 1), created_by=self.admin)
        from apps.finance.locking import save_internal
        from apps.finance.models import DistributionPolicyShare
        for sh in self.shareholders:
            save_internal(DistributionPolicyShare(policy=policy, stakeholder=sh, percent=Decimal('25')))
        month = self.period()
        self.count_all(month)
        with patch('django.utils.timezone.now', return_value=AUGUST):
            close_period(period_id=month.pk, actor=self.admin)
            profit = ProfitPeriod.objects.get(finance_period=month)
            propose_distribution(profit_id=profit.pk, amount=Decimal('100'), actor=self.admin)
            approve_distributions(profit_id=profit.pk, amount=Decimal('100'), actor=self.admin)
        screen = build_report(user=self.admin, kind='RESULT', preset='QUARTER', anchor=date(2026, 8, 1))
        self.assertEqual(screen['status'], 'PROVISOIRE')
        self.assertEqual(build_report(user=self.admin, kind='RESULT', preset='MONTH', anchor=date(2026, 7, 1))['status'], 'CLÔTURÉ')
        exports = {fmt: generate_finance_report(user=self.admin, kind='RESULT', preset='QUARTER', anchor=date(2026, 8, 1), format=fmt) for fmt in ('CSV', 'XLSX', 'PDF')}
        for export in exports.values():
            self.assertEqual(export.status, 'READY')
            self.assertEqual(export.snapshot['sections'][0]['rows'][0][8], _text(screen['sections'][0]['rows'][0][8]))
            self.assertEqual(Decimal(export.snapshot['sections'][0]['rows'][0][8].replace(' ', '')), profit.net_profit)
        csv_text = exports['CSV'].file.open('rb').read().decode('utf-8-sig')
        self.assertIn('Résultat mensuel'.upper(), csv_text)
        self.assertIn(str(profit.net_profit), csv_text)
        self.assertEqual(safe_cell('=HYPERLINK("x")'), "'=HYPERLINK(\"x\")")
        self.assertEqual(safe_cell('-2'), "'-2")
        self.assertEqual(safe_cell(Decimal('-2')), Decimal('-2'))
