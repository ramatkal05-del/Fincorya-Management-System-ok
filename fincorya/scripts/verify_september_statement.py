"""Read-only checks of totals, reconciliation, rendering and access control."""
import os
import sys
from decimal import Decimal
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, override_settings
from apps.accounts.models import User
from apps.expenses.models import Expense
from apps.operations.models import Operation
from apps.reports.month_end import build_month_end_statement
from apps.reports.views import month_end_statement, report_center
from apps.accounts.views import dashboard

before=(Operation.objects.count(),Expense.objects.count())
report=build_month_end_statement()
assert report['cash_total']==8969
assert report['totals']['commission']==1340
assert report['totals']['expense']==288
assert report['totals']['partner_commission']==296
assert report['totals']['fincorya_partner_share']==Decimal('118.40')
assert report['totals']['fantiny_share']==Decimal('177.60')
assert report['totals']['declared_result']==Decimal('1170.40')
assert report['totals']['actual_commission']==1340
assert report['totals']['actual_partner']==291
assert any(r['week']==1 and r['delta']==-5 for r in report['discrepancies'])
factory=RequestFactory()
with override_settings(LOCAL_AUTH_BYPASS=False):
    req=factory.get('/rapports/situation-septembre-2026/')
    req.user=User.objects.get(email='fincoryagroup@gmail.com')
    req.session={}
    response=month_end_statement(req)
    assert response.status_code==200
    home=dashboard(req)
    assert home.status_code==200
    assert 'Situation de septembre 2026' not in home.content.decode()
    assert 'Dernières opérations' in home.content.decode()
    activity_req=factory.get('/?view=activity')
    activity_req.user=req.user
    activity_req.session={}
    activity=dashboard(activity_req)
    assert activity.status_code==200 and 'Dernières opérations' in activity.content.decode()
    html=response.content.decode()
    for phrase in ['Rapport général du mois de septembre','GO TRAVEL (Fantiny)','Rapprochement avec les écritures','Situation de la carte','118,40','177,60']:
        assert phrase in html,phrase
    out=Path('output/week4/situation-septembre-verifiee.html')
    out.write_text(html,encoding='utf-8')
    req.htmx=False
    centre=report_center(req)
    assert centre.status_code==200 and b'situation-septembre-2026/' in centre.content
    req.user=AnonymousUser()
    assert month_end_statement(req).status_code==302
    req.user=User.objects.filter(role='AGENT',is_active=True).first()
    agent_home=dashboard(req)
    assert agent_home.status_code==200
    assert 'Situation de septembre 2026' not in agent_home.content.decode()
    try:
        month_end_statement(req)
        raise AssertionError('Agent received the admin statement')
    except PermissionDenied:
        pass
assert before==(Operation.objects.count(),Expense.objects.count())
print('PASS: totals, exact partner split, visible discrepancies, rendered report, centre link, anonymous redirect, agent denied, operations/expenses unchanged.')
