"""Explicit, atomic opening snapshot; never deletes financial history."""
import json
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from apps.finance.locking import ledger_atomic

KEY = 'OCTOBER-2026-01-11-RESTART-V1'
START, END = date(2026, 10, 1), date(2026, 10, 11)
TARGETS = {'victoria': '3096.00', 'david': '2444.00', 'chris': '1428.00', 'grace': '1025.00'}
# Comptes confirmés sur Render (apps/accounts) ; l'e-mail est la clé fiable,
# les anciens mots-clés de nom (« victoria », « chris ») ne correspondaient pas
# à « Victorine » et « Christ » et échouaient silencieusement en ambiguïté.
EMAILS = {'victoria': 'thondavictorine@gmail.com', 'david': 'nsukudav@gmail.com',
          'chris': 'christkapia1@gmail.com', 'grace': 'tubadigrace6@gmail.com'}


class Command(BaseCommand):
    help = 'Prévisualise la reprise octobre ; --apply applique une seule fois, sans supprimer l’historique.'

    def add_arguments(self, parser):
        parser.add_argument('--admin', default='fincoryagroup@gmail.com', help='E-mail exact de l’administrateur.')
        for name in TARGETS:
            parser.add_argument('--' + name, type=int, help='ID utilisateur exact, sinon résolution par nom unique.')
        parser.add_argument('--apply', action='store_true')

    @ledger_atomic
    def handle(self, *args, **options):
        from apps.accounts.models import User
        from apps.audit.services import record
        from apps.cash.models import CashAccount, DailyClosure
        from apps.cash.services import apply_movement
        from apps.expenses.models import Expense
        from apps.expenses.services import decide_expense
        from apps.finance.cutover import active_cutover
        from apps.finance.events import record_cash_adjustment
        from apps.finance.models import FinancialAccount, FinancialPeriod, ImportRow, FundContribution
        from apps.finance.services import ledger_balance
        from apps.operations.models import Operation
        from apps.pricing.models import Currency
        from apps.profits.models import ProfitPeriod
        from config.business_time import business_day_bounds

        admin = User.objects.filter(email__iexact=options['admin'], role='ADMIN', is_active=True).first()
        if admin is None:
            raise CommandError('Administrateur actif introuvable.')
        for name, email in EMAILS.items():
            if options[name] is None:
                agent = User.objects.filter(email__iexact=email, role='AGENT', is_active=True).first()
                if agent is None:
                    raise CommandError(f'{name}: aucun agent actif avec l’e-mail {email}. Fournir --{name} ID_UTILISATEUR exact.')
                options[name] = agent.pk
        ids = [options[name] for name in TARGETS]
        if len(set(ids)) != 4:
            raise CommandError('Les quatre agents doivent être distincts.')
        request = {name: {'user_id': options[name], 'target': target} for name, target in TARGETS.items()}
        previous = ImportRow.objects.filter(source_key=KEY).first()
        if previous:
            if previous.payload['request'] != request:
                raise CommandError('Cette reprise a déjà été appliquée avec une autre correspondance agents.')
            self.stdout.write('Déjà appliqué : aucune remise à zéro des opérations ou soldes ultérieurs.')
            return
        usd = Currency.objects.get(code='USD')
        lower, upper = business_day_bounds(START, END)
        # Opérations réelles de l'application pendant la préparation de la reprise :
        # elles restent dans le grand livre (jamais supprimées ni réimportées) et sont
        # déjà reflétées dans le solde actuel de chaque caisse ; l'ajustement ci-dessous
        # part de ce solde réel, il ne le remplace pas. Ce rapport les rend visibles,
        # il ne bloque plus une activité normale pendant la préparation.
        existing_ops = Operation.objects.filter(created_at__gte=lower, created_at__lt=upper, currency=usd)
        existing_operations_in_window = list(existing_ops.values('agent__email', 'status')
            .annotate(count=Count('id'), amount=Sum('amount'), fee=Sum('fee'))
            .order_by('agent__email', 'status'))
        if Expense.objects.filter(incurred_on__range=(START, END), currency=usd).exists():
            raise CommandError('Des charges USD existent déjà dans cette période. Rapprocher les 141 USD avant reprise.')
        if FinancialPeriod.objects.filter(status='LOCKED', start_date__lte=END, end_date__gte=START).exists():
            raise CommandError('Une période chevauchante est verrouillée.')
        periods = FinancialPeriod.objects.filter(period_type='WEEK', start_date__lte=END, end_date__gte=START)
        if periods.exclude(start_date=START, end_date=END).exists():
            raise CommandError('Des sous-périodes existent déjà : fusion contrôlée nécessaire, aucune période effacée.')
        if ProfitPeriod.objects.filter(start_date__lte=END, end_date__gte=START).exclude(start_date=START, end_date=END).exists():
            raise CommandError('Des périodes de bénéfice chevauchantes existent déjà.')
        profit = ProfitPeriod.objects.filter(start_date=START, end_date=END, currency=usd).first()
        if profit and (profit.status != 'DRAFT' or profit.allocations.exists() or profit.snapshot or profit.proposed_distribution):
            raise CommandError('La période de bénéfice contient déjà des résultats ou distributions.')
        engine = active_cutover()
        accounts, changes = [], []
        for name, target in TARGETS.items():
            account = CashAccount.objects.select_for_update().select_related('agent').filter(agent_id=options[name], currency=usd).first()
            if not account or account.agent.role != 'AGENT' or not account.agent.is_active:
                raise CommandError(f'{name}: caisse USD et utilisateur AGENT actif requis (ID {options[name]}).')
            if account.is_locked or DailyClosure.objects.filter(account=account, business_date__gte=START).exists():
                raise CommandError(f'{name}: caisse verrouillée ou comptage existant depuis le 1 octobre.')
            financial = FinancialAccount.objects.filter(legacy_cash_account=account).first()
            if financial:
                balance = ledger_balance(financial)
                if financial.cached_balance != balance or balance != account.balance:
                    raise CommandError(f'{name}: solde caisse/grand livre divergent ; rapprochement requis.')
                if not engine:
                    raise CommandError(f'{name}: compte financier existant mais bascule inactive ; reprise refusée pour éviter une divergence.')
            elif engine:
                raise CommandError(f'{name}: compte financier de caisse manquant après bascule.')
            accounts.append((account, Decimal(target)))
            changes.append(dict(agent=name, actual_name=account.agent.get_full_name(), user_id=account.agent_id,
                account_id=account.pk, before=str(account.balance), after=target, delta=str(Decimal(target)-account.balance)))
        report = dict(request=request, period=[str(START), str(END)], cash=changes, total='7993.00',
            expenses='141.00', source='Jenovich (déclaration utilisateur)',
            accounting='Reprise déclarée des soldes, pas un nouveau transfert de 6000 USD. Corrections hors commissions.',
            expense_payment='Charges reconnues, paiement non enregistré : compte payeur non identifié. Aucun débit des quatre caisses.',
            contributions=list(FundContribution.objects.filter(currency=usd).values('stakeholder__name', 'origin', 'amount')),
            stakeholder_policy='Apports historiques conservés : le capital existant ne constitue pas un nouveau versement.',
            effective_cash_time=str(timezone.now()), operations_imported=0,
            existing_operations_in_window=existing_operations_in_window,
            existing_operations_note='Opérations réelles déjà enregistrées par l’application du 1 au 11 octobre. '
                'Conservées telles quelles dans le grand livre ; les soldes avant/après ci-dessus en tiennent déjà compte.')
        if not options['apply']:
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            transaction.set_rollback(True)
            return
        period, _ = FinancialPeriod.objects.get_or_create(period_type='WEEK', start_date=START, end_date=END,
            defaults={'timezone_name': 'Europe/Istanbul', 'status': 'OPEN'})
        note = 'Reprise octobre autorisée : solde déclaré après ravitaillement Jenovich ; aucune opération Excel importée.'
        for account, target in accounts:
            delta = target-account.balance
            if delta:
                movement = apply_movement(account=account, direction='IN' if delta > 0 else 'OUT',
                    amount=abs(delta), movement_type='ADJUSTMENT', actor=admin, note=note)
                record_cash_adjustment(movement, admin)
            account.is_active = True
            account.allocated_by = admin
            account.allocated_at = timezone.now()
            account.save(update_fields=['is_active', 'allocated_by', 'allocated_at'])
            account.refresh_from_db()
            if account.balance != target:
                raise CommandError('Le contrôle du solde final a échoué.')
        for suffix, amount, label in [('SEND', '126', 'Frais envoi MoneyGram du ravitaillement de 6000 USD'),
                                      ('CYPRUS', '15', 'Commission MoneyGram Chypre du ravitaillement')]:
            expense = Expense.objects.create(accrual_key=KEY+'-'+suffix, label=label, category='OPERATIONS',
                amount=Decimal(amount), currency=usd, incurred_on=date(2026,10,4), created_by=admin)
            decide_expense(expense_id=expense.pk, actor=admin, decision='APPROVED',
                comment='Déclaration utilisateur ; regroupement au 04/10 selon rapport. Aucun paiement agent recréé.')
        gross_fees = existing_ops.filter(status='COMPLETED').aggregate(v=Sum('fee'))['v'] or Decimal('0.00')
        profit, _ = ProfitPeriod.objects.get_or_create(start_date=START, end_date=END, currency=usd,
            defaults={'finance_period': period, 'calculated_by': admin})
        profit.finance_period = period
        profit.gross_fees = gross_fees
        profit.deductible_expenses = Decimal('141')
        profit.net_profit = gross_fees - Decimal('141')
        profit.distributable = max(profit.net_profit, Decimal('0.00'))
        profit.save()
        payload = json.loads(json.dumps(report, default=str))
        row = ImportRow.objects.create(source_key=KEY, payload=payload, reviewed_by=admin,
            resolution='Reprise explicitement demandée ; soldes remplacés par mouvements tracés ; historique conservé.')
        record(actor=admin, action='OCTOBER_RESTART', instance=row, after=payload)
        self.stdout.write(self.style.SUCCESS('Appliqué : période 01–11 octobre, caisses 7993 USD, charges 141 USD. Aucune opération importée.'))
