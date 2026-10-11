"""Read-only comparison of OCTOBRE 1.xlsx with the selected database."""
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum


class Command(BaseCommand):
    help = "Contrôle OCTOBRE 1 : lecture seule, aucune importation ni suppression."

    def add_arguments(self, parser):
        parser.add_argument('--start', required=True, type=date.fromisoformat)
        parser.add_argument('--end', required=True, type=date.fromisoformat)

    def handle(self, *args, **options):
        from apps.accounts.models import User
        from apps.cash.models import CashAccount
        from apps.finance.models import FundContribution, FinancialPeriod
        from apps.operations.models import Operation
        from apps.stakeholders.models import Investment
        from config.business_time import business_day_bounds

        start, end = options['start'], options['end']
        if start > end:
            raise CommandError('La date de début doit précéder la date de fin.')
        source = Path(__file__).resolve().parents[2] / 'data' / 'octobre_1_2026.json'
        data = json.loads(source.read_text(encoding='utf-8'))
        total = lambda rows, key: sum((Decimal(r[key]) for r in rows), Decimal('0'))
        agents = []
        for row in data['cash']:
            candidates = []
            # Candidates only: abbreviated names never constitute an approved mapping.
            for user in User.objects.all().only('pk', 'first_name', 'last_name', 'username', 'role', 'is_active'):
                words = (user.first_name + ' ' + user.last_name + ' ' + user.username).upper().split()
                if any(word.startswith(row['agent']) for word in words):
                    candidates.append(dict(user_id=user.pk, name=user.get_full_name(), role=user.role,
                        active=user.is_active, cash=list(CashAccount.objects.filter(agent=user, currency__code='USD')
                            .values('id', 'balance', 'is_active', 'is_locked'))))
            ops = [r for r in data['operations'] if r['agent'] == row['agent']]
            # Illustrations of existing service semantics, never reconciliation entries.
            net = total(ops, 'send') - total(ops, 'withdrawal')
            send_fees = total([r for r in ops if Decimal(r['send'])], 'commission')
            withdrawal_fees = total([r for r in ops if Decimal(r['withdrawal'])], 'commission')
            opening, closing = Decimal(row['opening']), Decimal(row['closing'])
            scenarios = {}
            for send_mode in ('ADDED', 'DEDUCTED'):
                for withdrawal_mode in ('ADDED', 'DEDUCTED'):
                    theoretical = opening + net + (send_fees if send_mode == 'ADDED' else 0) + (withdrawal_fees if withdrawal_mode == 'DEDUCTED' else 0)
                    scenarios[f'{send_mode}/{withdrawal_mode}'] = dict(theoretical=theoretical,
                        declared_minus_theoretical=closing-theoretical)
            agents.append(dict(source=row, candidates=candidates,
                personal_advance_declared=max(-closing, Decimal('0')), scenarios_without_other_movements=scenarios))
        lower, upper = business_day_bounds(start, end)
        existing = Operation.objects.filter(created_at__gte=lower, created_at__lt=upper, currency__code='USD')
        result = dict(mode='READ_ONLY_NO_IMPORT_NO_RESET', source_file=data['source_file'],
            source_sha256=data['sha256'], requested_period=[start, end],
            declared=dict(capital=total(data['capital'], 'amount'), operations=len(data['operations']),
                sent=total(data['operations'], 'send'), withdrawals=total(data['operations'], 'withdrawal'),
                commissions=total(data['operations'], 'commission'), expenses=total(data['expenses'], 'amount')),
            issues=data['issues'], agents=agents,
            existing_operations=list(existing.values('agent_id','status').annotate(count_sum=Sum('amount'), commissions=Sum('fee'))),
            existing_operation_count=existing.count(),
            overlapping_periods=list(FinancialPeriod.objects.filter(start_date__lte=end,end_date__gte=start)
                .values('id','start_date','end_date','status')),
            contributions=list(FundContribution.objects.filter(currency__code='USD').values('stakeholder_id','origin').annotate(total=Sum('amount'))),
            legacy_investments=list(Investment.objects.filter(currency__code='USD').values('stakeholder_id').annotate(total=Sum('amount'))),
            warning='Ne pas additionner les anciens investissements et leurs apports de remplacement. Aucun écart ne constitue une autorisation de correction.')
        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2, default=str))
