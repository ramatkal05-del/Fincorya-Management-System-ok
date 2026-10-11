"""Store the user's declared statement without changing posted operations."""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.db import transaction
from apps.accounts.models import User
from apps.audit.services import record
from apps.finance.models import ImportRow
from apps.stakeholders.models import Stakeholder
from apps.reports.month_end import SOURCE_KEY, build_month_end_statement

with transaction.atomic():
    admin=User.objects.get(email='fincoryagroup@gmail.com',role='ADMIN')
    partner=Stakeholder.objects.get(name='Fantiny Pelenda')
    payload={'year':2026,'month':9,'currency':'USD','partner_id':partner.pk,'partner_display_name':'GO TRAVEL (Fantiny)',
             'fincorya_share_percent':'40',
             'last_days':{'count':9,'rdc':360,'cyprus':112,'commission':67},
             'cash':[{'name':name,'opening':opening,'closing':closing,'city':city,'country':country} for name,opening,closing,city,country in [
                 ('VICTORIA',393,556,'LEFKOSA','CHYPRE'),('DAV',357,329,'LEFKOSA','CHYPRE'),('CHRIS',1054,1215,'GIRNE','CHYPRE'),('GLODIE',5850,6869,'KINSHASA','RDC')]],
             'weeks':[dict(number=i,start=start,end=end,expense=expense,commission=commission,partner_commission=partner_fee) for i,start,end,expense,commission,partner_fee in [
                 (1,1,6,6,305,94),(2,7,13,175,343,86),(3,14,20,0,268,15),(4,21,27,107,357,101),(5,28,30,0,67,0)]],
             'card_note':"La situation de la carte n’est pas encore résolue : faute de cash, les fonds ont été utilisés pour combler le manque de trésorerie. Au prochain ravitaillement, le département Finance versera le montant demandé."}
    row,created=ImportRow.objects.get_or_create(source_key=SOURCE_KEY,defaults={'payload':payload,'reviewed_by':admin,'resolution':'Situation déclarée utilisateur ; rapprochement affiché séparément, sans altération des écritures.'})
    if not created and row.payload != payload:
        raise RuntimeError('Une autre version de la situation existe ; révision explicite nécessaire.')
    if created:
        record(actor=admin,action='SEPTEMBER_STATEMENT_CAPTURE',instance=row,after=payload)
    result=build_month_end_statement()
    assert result['cash_total']==8969
    assert result['totals']['commission']==1340 and result['totals']['expense']==288
    assert result['totals']['partner_commission']==296
    print('Situation enregistrée ; cash',result['cash_total'],'; totaux',result['totals'])
    print('Écarts',result['discrepancies'])
