"""Preserve supplied September W4 data; open period only, no cash postings."""
import json
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# source order: network, send, withdrawal, agent, commission
SOURCE = '''Orange,0,21,DAV,5
Vodacom,42,0,RUTH,8
Vodacom,45,0,RUTH,8
Vodacom,0,92,CHRIS,8
Airtel,15,0,DAV,5
Airtel,40,0,DAV,5
Airtel,32,0,CHRIS,5
Vodacom,0,660,DAV,35
Vodacom,0,1336,RUTH,64
Vodacom,20,0,VICTORIA,5
PayPal,62,0,DAV,8
Vodacom,50,0,CHRIS,8
Airtel,0,500,VICTORIA,30
Vodacom,10,0,RUTH,5
Airtel,0,67,VICTORIA,8
Vodacom,0,570,VICTORIA,30
PayPal,0,228,DAV,20
Vodacom,200,0,VICTORIA,15
Vodacom,10,0,DAV,5
Airtel,0,38,VICTORIA,5
Vodacom,30,0,VICTORIA,5
Vodacom,0,100,CHRIS,8
Vodacom,0,52,CHRIS,8
Orange,35,0,CHRIS,5
Vodacom,0,100,DAV,10
Orange,0,92,VICTORIA,8
Vodacom,100,0,CHRIS,8
Orange,50,0,CHRIS,8
Vodacom,110,0,CHRIS,15
Vodacom,350,0,CHRIS,20
Airtel,0,108,VICTORIA,8
Airtel,0,280,VICTORIA,20
Vodacom,0,50,DAV,8
Orange,185,0,CHRIS,15
Vodacom,0,400,DAV,30
Airtel,25,0,VICTORIA,5
Airtel,0,25,DAV,5
Airtel,0,42,CHRIS,8
Vodacom,0,45,DAV,8
Vodacom,20,0,VICTORIA,5
Airtel,100,0,VICTORIA,8
Vodacom,35,0,DAV,5
Airtel,50,0,CHRIS,8
Airtel,130,0,CHRIS,15'''

def prepare():
    rows = []
    for i, line in enumerate(SOURCE.splitlines(), 1):
        network, send, withdrawal, agent, commission = line.split(',')
        rows.append(dict(id=i, source_number=i if i <= 35 else i-35,
                         network=network, send=int(send), withdrawal=int(withdrawal),
                         agent=agent, commission=int(commission), date=None,
                         source_block='principal' if i <= 29 else 'Fantiny' if i <= 35 else 'trois derniers jours'))
    assert len(rows) == 44
    balances = {'VICTORIA': [1330,393,556], 'DAV': [2051,357,329],
                'CHRIS': [1089,1054,1215], 'RUTH': [721,0,None], 'GLODIE': [4296,5850,6869]}
    expenses = {'DAV':20, 'CHRIS':87}
    summary = {}
    for agent, balances_for_agent in balances.items():
        selected = [r for r in rows if r['agent'] == agent]
        totals = {k:sum(r[k] for r in selected) for k in ('send','withdrawal','commission')}
        summary[agent] = dict(count=len(selected), **totals, declared_balances=balances_for_agent,
                              expense=expenses.get(agent,0))
        if agent != 'GLODIE':
            theoretical = balances_for_agent[0]+totals['send']-totals['withdrawal']+totals['commission']-expenses.get(agent,0)
            summary[agent]['theoretical_final_if_fees_added_send_deducted_withdrawal'] = theoretical
            summary[agent]['declared_minus_theoretical'] = None if balances_for_agent[2] is None else balances_for_agent[2]-theoretical
    payload = dict(period=['2026-09-21','2026-09-30'], currency='USD',
                   status='PREPARATION_NON_COMPTABILISEE', operations=rows, agents=summary,
                   expenses={'description':'Réunion agents : consommation, impression et transport', 'amount':107, 'cash_sources':expenses, 'date':None},
                   unresolved=['Dates exactes ou dates de regroupement', 'Convention des commissions et attribution Fantiny',
                               'Mouvements de caisse manquants et réapprovisionnement Ruth', 'Périmètre et détail des comptes Glodie', 'Solde final Ruth non fourni'])
    out = ROOT/'output'/'week4'
    out.mkdir(parents=True, exist_ok=True)
    (out/'donnees_preparees.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    for agent in summary:
        (out/f'{agent.lower()}.json').write_text(json.dumps(dict(agent=agent,summary=summary[agent],operations=[r for r in rows if r['agent']==agent]),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print('TOTALS', {k:sum(r[k] for r in rows) for k in ('send','withdrawal','commission')})
    return payload

def open_period():
    import django
    django.setup()
    from django.db import transaction
    from apps.finance.models import FinancialPeriod, PeriodType, PeriodStatus
    from apps.profits.models import ProfitPeriod, ProfitStatus
    from apps.pricing.models import Currency
    from apps.accounts.models import User, Role
    with transaction.atomic():
        start, end = date(2026,9,21), date(2026,9,30)
        overlaps = FinancialPeriod.objects.filter(period_type=PeriodType.WEEK,start_date__lte=end,end_date__gte=start)
        if overlaps.exclude(start_date=start,end_date=end).exists():
            raise RuntimeError('Overlapping weekly period; manual review needed')
        fp, created = FinancialPeriod.objects.get_or_create(period_type=PeriodType.WEEK,start_date=start,end_date=end,defaults={'status':PeriodStatus.OPEN,'timezone_name':'Europe/Istanbul'})
        if fp.status != PeriodStatus.OPEN:
            raise RuntimeError('Existing period is locked')
        pp, _ = ProfitPeriod.objects.get_or_create(start_date=start,end_date=end,currency=Currency.objects.get(code='USD'),defaults={'finance_period':fp,'status':ProfitStatus.DRAFT,'calculated_by':User.objects.filter(role=Role.ADMIN).first()})
        print(f'Financial period {fp.pk}: {fp.status}; created={created}; profit period {pp.pk}: {pp.status}')

if __name__ == '__main__':
    prepare()
    if '--open-period' in sys.argv:
        open_period()
