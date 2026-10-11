"""Audited local September import. Default is a full transaction rollback."""
import argparse
import json
import os
import sys
from datetime import datetime, date, timedelta
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.conf import settings
from django.core import serializers
from django.db import transaction
from django.db.models import Sum
from apps.accounts.models import User
from apps.audit.services import record
from apps.cash.models import CashAccount, CashMovement
from apps.cash.services import apply_movement, close_cash_day
from apps.expenses.models import Expense
from apps.expenses.services import decide_expense
from apps.finance.models import FinancialAccount, FinancialPeriod, ImportRow, JournalBatch
from apps.finance.events import counterpart, line, record_cash_adjustment
from apps.finance.services import prepare_batch, post_batch
from apps.finance.cutover import active_cutover
from apps.notifications.services import notify
from apps.notifications.models import NotificationDelivery
from apps.operations.models import Operation
from apps.operations.services import create_sent_transfer, create_withdrawal
from apps.pricing.models import Currency
from apps.profits.models import ProfitPeriod
from apps.stakeholders.models import Stakeholder, PartnerOperation

TZ = ZoneInfo('Europe/Istanbul')
def when(day, hour=12, minute=0):
    return datetime(2026,9,day,hour,minute,tzinfo=TZ)

def local_notify(**kwargs):
    kwargs['email'] = False
    return notify(**kwargs)

def run(fee_mode, commit):
    assert settings.DATABASES['default'].get('HOST') in ('localhost','127.0.0.1','::1',''), 'Local database only'
    assert active_cutover() is None, 'This import targets the existing legacy local database only'
    source = json.loads((ROOT/'output/week4/donnees_preparees.json').read_text(encoding='utf-8'))
    marker = 'W4-20260921-30-COMPLETED'
    if ImportRow.objects.filter(source_key=marker).exists():
        existing = ImportRow.objects.get(source_key=marker).payload
        assert existing['send_fee_mode'] == fee_mode
        print(json.dumps(existing,ensure_ascii=False,indent=2))
        print('Already imported: no changes')
        return
    admin = User.objects.get(pk=5)
    usd = Currency.objects.get(code='USD')
    period = FinancialPeriod.objects.get(start_date=date(2026,9,21),end_date=date(2026,9,30),period_type='WEEK')
    assert period.status == 'OPEN'
    codes = {'DAV':6,'RUTH':7,'VICTORIA':8,'CHRIS':9}
    accounts = {code:CashAccount.objects.get(agent_id=pk,currency=usd) for code,pk in codes.items()}
    partner = Stakeholder.objects.get(name='Fantiny Pelenda')
    glodie = Stakeholder.objects.get(name='Glodie Kongolo')
    networks = {'Orange':'ORANGE_MONEY','Vodacom':'VODACOM_MPESA','Airtel':'AIRTEL_MONEY','PayPal':'PAYPAL'}
    prior = list(Operation.objects.filter(created_at__gte=when(21,0),created_at__lt=when(30,0)+timedelta(days=1)).values('pk','amount','fee','status','created_at'))
    assert len(prior) == 1 and prior[0]['amount'] == 135 and prior[0]['fee'] == 15
    if commit:
        backup = ROOT/'output/week4/etat_avant_import.json'
        if not backup.exists():
            objects = [*CashAccount.objects.all(),*FinancialAccount.objects.all(),*ProfitPeriod.objects.filter(pk=4),*Operation.objects.filter(pk=prior[0]['pk'])]
            backup.write_text(serializers.serialize('json',objects,indent=2),encoding='utf-8')
    adjustments = []
    deliveries_before = NotificationDelivery.objects.count()
    closures = []
    operation_ids = []
    with transaction.atomic(), patch('apps.operations.services.notify',local_notify), patch('apps.cash.services.notify',local_notify):
        for code, acc in accounts.items():
            assert acc.balance == D(source['agents'][code]['declared_balances'][0]), f'Opening changed: {code}'
        def adjust(code, amount, note):
            acc = CashAccount.objects.select_for_update().get(pk=accounts[code].pk)
            movement = apply_movement(account=acc,direction='IN' if amount>0 else 'OUT',amount=abs(amount),movement_type='ADJUSTMENT',actor=admin,note=note)
            record_cash_adjustment(movement,admin)
            record(actor=admin,action='W4_SIMULATION_ADJUSTMENT',instance=movement,after={'amount':str(amount),'reason':note})
            adjustments.append({'agent':code,'amount':str(amount),'reason':note,'movement_id':movement.pk})

        # Keep unknown Glodie allocation separate from existing network balances.
        known = list(FinancialAccount.objects.filter(economic_owner=glodie,account_type__in=['AGENT_CASH','MOBILE_MONEY','DIGITAL','FINANCIAL_SERVICE']))
        known_total = sum(a.cached_balance for a in known)
        unallocated, created = FinancialAccount.objects.get_or_create(code='UNALLOC-GLODIE-USD',defaults=dict(name='Glodie - trésorerie déclarée à ventiler (simulation)',account_type='FINANCIAL_SERVICE',nature='ASSET',currency=usd,economic_owner=glodie,responsible_user=glodie.owner,notes='Résiduel agrégé cash et comptes électroniques. Répartition non fournie ; ne représente pas un réseau confirmé.'))
        assert created
        adj_account = counterpart(usd,'ADJUSTMENT')
        last_total = known_total
        for day,hour,target,label in [(21,8,4296,'ouverture'),(27,18,5850,'intermédiaire'),(30,18,6869,'final')]:
            delta = D(target)-last_total
            with patch('django.utils.timezone.now',return_value=when(day,hour)):
                batch = prepare_batch(actor=admin,idempotency_key=f'W4-GLODIE-{label}',event_type='SIMULATION_ADJUSTMENT',effective_at=when(day,hour),description=f'Glodie W4 {label}: rapprochement total déclaré, ventilation inconnue',lines=[line(unallocated,'DEBIT' if delta>0 else 'CREDIT',abs(delta)),line(adj_account,'CREDIT' if delta>0 else 'DEBIT',abs(delta))])
                post_batch(batch_id=batch.pk,actor=admin)
            last_total = D(target)
        for block in (source['operations'][:35],source['operations'][35:]):
            day = 27 if block[0]['id']==1 else 30
            for i,row in enumerate(block):
                with patch('django.utils.timezone.now',return_value=when(day,10,i)):
                    acc = CashAccount.objects.get(pk=accounts[row['agent']].pk)
                    amount = D(row['send'] or row['withdrawal'])
                    fee = D(row['commission'])
                    if row['withdrawal'] and acc.balance < amount-fee:
                        adjust(row['agent'],amount-fee-acc.balance,'Simulation locale autorisée : liquidité manquante avant retrait ; origine non documentée, régularisation à justifier.')
                    fn = create_sent_transfer if row['send'] else create_withdrawal
                    op = fn(agent=acc.agent,account_id=acc.pk,amount=amount,tariff_schedule=None,manual_fee=fee,fee_mode=fee_mode if row['send'] else 'DEDUCTED',service=networks[row['network']],stakeholder=partner if row['source_block']=='Fantiny' else None,commission_owner_confirmed=True,idempotency_key=f'W4-202609-{row["id"]}',note=f'Simulation locale W4 #{row["id"]}; date de regroupement autorisée {day}/09/2026; montant source {amount} USD; bloc {row["source_block"]}.')
                    operation_ids.append(op.pk)
                    expected_amount = amount-fee if row['send'] and fee_mode=='DEDUCTED' else amount
                    assert op.amount==expected_amount and op.fee==fee
                    attribution = getattr(op,'partner_attribution',None)
                    if attribution:
                        attribution.share_percent = partner.partner_share_percent
                        attribution.save(update_fields=['share_percent'])
                    ImportRow.objects.create(source_key=f'W4-SOURCE-{row["id"]}',payload={**row,'operation_id':op.pk,'effective_date':str(op.created_at.date()),'fee_mode':op.fee_mode},reviewed_by=admin,resolution='Données utilisateur ; date regroupée autorisée ; import local contrôlé.')
            if day==27:
                with patch('django.utils.timezone.now',return_value=when(27,16)):
                    for code,amount in [('DAV',20),('CHRIS',87)]:
                        expense = Expense.objects.create(accrual_key=f'W4-REUNION-{code}',label=f'Réunion agents : consommation, impression et transport — {code}',category='OPERATIONS',agent_id=codes[code],amount=D(amount),currency=usd,incurred_on=date(2026,9,27),created_by=admin)
                        decide_expense(expense_id=expense.pk,actor=admin,decision='APPROVED',comment='Dépense fournie par utilisateur ; date regroupée de simulation.')
                        acc=CashAccount.objects.select_for_update().get(pk=accounts[code].pk)
                        movement=apply_movement(account=acc,direction='OUT',amount=D(amount),movement_type='ADJUSTMENT',actor=admin,note=f'Paiement effectif charge #{expense.pk} W4 réunion agents (moteur legacy).')
                        record(actor=admin,action='W4_EXPENSE_PAYMENT',instance=expense,after={'cash_movement_id':movement.pk,'amount':str(amount),'agent':code})
            with patch('django.utils.timezone.now',return_value=when(day,19)):
                for code,acc in accounts.items():
                    target=source['agents'][code]['declared_balances'][1 if day==27 else 2]
                    target=0 if code=='RUTH' and day==30 else target
                    closure=close_cash_day(account_id=acc.pk,business_date=date(2026,9,day),declared_cash=D(target),closed_by=admin,justification='Simulation locale autorisée : reprise du solde déclaré utilisateur. Écart non expliqué par les opérations, régularisation de comptage ; aucun transfert réel supposé.')
                    closures.append({'agent':code,'date':str(closure.business_date),'expected':str(closure.expected_balance),'declared':str(closure.declared_cash),'adjustment':str(closure.variance)})
        ops=Operation.objects.filter(pk__in=operation_ids)
        assert ops.count()==44 and ops.aggregate(v=Sum('fee'))['v']==525
        assert PartnerOperation.objects.filter(operation__in=ops,stakeholder=partner).count()==6
        assert sum(x['commission'] for x in source['operations'][29:35])==101
        assert Expense.objects.filter(accrual_key__startswith='W4-REUNION-').aggregate(v=Sum('amount'))['v']==107
        for code,target in [('VICTORIA',556),('DAV',329),('CHRIS',1215),('RUTH',0)]:
            acc=CashAccount.objects.get(pk=accounts[code].pk)
            assert acc.balance==target
            assert acc.movements.order_by('created_at','pk').last().balance_after==target
        unallocated.refresh_from_db()
        assert known_total+unallocated.cached_balance==6869
        for original in known:
            unchanged=FinancialAccount.objects.get(pk=original.pk)
            assert unchanged.cached_balance==original.cached_balance
        assert list(Operation.objects.filter(pk=prior[0]['pk']).values('pk','amount','fee','status','created_at'))==prior
        pp=ProfitPeriod.objects.select_for_update().get(finance_period=period)
        assert pp.status=='DRAFT' and not pp.allocations.exists()
        period_ops=Operation.objects.filter(created_at__gte=when(21,0),created_at__lt=when(30,0)+timedelta(days=1),status='COMPLETED',currency=usd)
        pp.gross_fees=period_ops.aggregate(v=Sum('fee'))['v']
        pp.deductible_expenses=Expense.objects.filter(incurred_on__range=(period.start_date,period.end_date),status='APPROVED',currency=usd).aggregate(v=Sum('amount'))['v']
        pp.net_profit=pp.gross_fees-pp.deductible_expenses
        assert period_ops.count()==45 and pp.gross_fees==540 and pp.deductible_expenses==107
        pp.snapshot={'simulation':True,'operation_ids':operation_ids+[prior[0]['pk']],'partner_gross_commissions':'101.00','partner_share_percent':str(partner.partner_share_percent),'partner_share':'60.60','after_partner_share_before_other_costs':'372.40','note':'Brouillon : résultat brut moins charges ; part partenaire présentée séparément ; régularisations hors résultat.'}
        pp.save()
        result={'status':'IMPORTED','send_fee_mode':fee_mode,'operations_imported':44,'operation_ids':operation_ids,'existing_distinct_operation_id':prior[0]['pk'],'period_status':'OPEN','fees_imported':525,'fees_period':540,'expenses':107,'partner_share':'60.60','closures':closures,'liquidity_adjustments':adjustments,'glodie_total':6869,'glodie_unallocated':str(unallocated.cached_balance)}
        ImportRow.objects.create(source_key='W4-GLODIE-DECLARED',payload={'opening':4296,'intermediate':5850,'final':6869,'scope':'cash + comptes électroniques','unallocated_account_id':unallocated.pk},reviewed_by=admin,resolution='Total conservé ; résiduel sur compte à ventiler, sans répartition inventée.')
        ImportRow.objects.create(source_key=marker,payload=result,reviewed_by=admin,resolution='Import simulé autorisé ; contrôles passés ; période laissée ouverte.')
        record(actor=admin,action='W4_IMPORT_COMPLETE',instance=period,after=result)
        assert NotificationDelivery.objects.count()==deliveries_before, 'No external notifications permitted'
        if not commit:
            transaction.set_rollback(True)
    name='resultat_import.json' if commit else 'simulation_import.json'
    (ROOT/'output/week4'/name).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    print('COMMITTED' if commit else 'DRY RUN: ROLLED BACK')

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--send-fee-mode',required=True,choices=['ADDED','DEDUCTED'])
    parser.add_argument('--commit',action='store_true')
    args=parser.parse_args()
    run(args.send_fee_mode,args.commit)
