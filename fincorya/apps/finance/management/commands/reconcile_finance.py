"""Read-only reconciliation; never sums the old receipts with their replacements."""
import json

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection, transaction

from apps.cash.models import CashAccount, GlobalCashAccount
from apps.finance.models import FinancialAccount, FundContribution
from apps.finance.services import ledger_balance
from apps.stakeholders.models import Investment


class Command(BaseCommand):
    help = 'Rapprochement en lecture seule des caisses, du grand livre et des anciens apports.'

    def handle(self, *args, **options):
        with transaction.atomic():
            if connection.vendor == 'postgresql':
                with connection.cursor() as cursor:
                    cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            accounts = []
            mapped_cash, mapped_global = set(), set()
            for account in FinancialAccount.objects.select_related('legacy_cash_account', 'legacy_global_account', 'currency'):
                legacy = account.legacy_cash_account or account.legacy_global_account
                if account.legacy_cash_account_id:
                    mapped_cash.add(account.legacy_cash_account_id)
                if account.legacy_global_account_id:
                    mapped_global.add(account.legacy_global_account_id)
                balance = ledger_balance(account)
                accounts.append(dict(code=account.code, currency=account.currency.code,
                    ledger=balance, cached=account.cached_balance,
                    cache_difference=account.cached_balance - balance,
                    legacy=legacy.balance if legacy else None,
                    legacy_difference=legacy.balance - balance if legacy else None))
            receipts = []
            for investment in Investment.objects.order_by('pk'):
                candidates = list(FundContribution.objects.filter(stakeholder_id=investment.stakeholder_id,
                    currency_id=investment.currency_id, amount=investment.amount,
                    received_on=investment.invested_on).values_list('pk', flat=True))
                receipts.append(dict(investment_id=investment.pk, possible_contribution_ids=candidates,
                    status='A_CONFIRMER' if candidates else 'SANS_CORRESPONDANCE'))
            result = dict(ledger_enabled=settings.FINANCE_LEDGER_ENABLED,
                migration_validated=False, accounts=accounts, legacy_receipts=receipts,
                unmapped_agent_accounts=list(CashAccount.objects.exclude(pk__in=mapped_cash).values_list('pk', flat=True)),
                unmapped_global_accounts=list(GlobalCashAccount.objects.exclude(pk__in=mapped_global).values_list('pk', flat=True)),
                note='Les correspondances sont des candidats, jamais une preuve de versement identique. Aucun solde ni apport modifié.')
            self.stdout.write(json.dumps(result, ensure_ascii=False, default=str, indent=2))
