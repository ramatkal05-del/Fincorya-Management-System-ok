"""Read-only inventory for a reset preserving users and original contributions."""
import json
from django.apps import apps
from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.conf import settings


class Command(BaseCommand):
    help = 'Aperçu uniquement : inventorie les données avant une remise à zéro conservant utilisateurs et apports. Aucune suppression.'

    def handle(self, *args, **options):
        from apps.finance.models import FundContribution, FinancialAccount
        from apps.stakeholders.models import Investment
        from apps.cash.models import CashAccount, GlobalCashAccount
        keep_apps = {'accounts', 'auth', 'contenttypes', 'sessions', 'otp_totp', 'pricing', 'contracts'}
        keep_models = {'stakeholders.stakeholder', 'stakeholders.stakeholderrole',
                       'stakeholders.investment', 'finance.fundcontribution',
                       'finance.economicrule', 'finance.remunerationterms',
                       'finance.partnerguarantee', 'operations.transactionserviceoption'}
        review_models = {'finance.financialaccount', 'finance.journalbatch', 'finance.ledgerentry',
                         'finance.legacymigrationrun', 'finance.accountreconciliation',
                         'cash.globalcashaccount', 'cash.globalcashmovement', 'audit.auditevent'}
        inventory = []
        for model in apps.get_models():
            label = model._meta.label_lower
            if model._meta.app_label in keep_apps or label in keep_models:
                treatment = 'CONSERVER'
            elif label in review_models:
                treatment = 'DEPENDANCES_A_EXAMINER'
            else:
                treatment = 'CANDIDAT_SUPPRESSION_A_CONFIRMER'
            inventory.append({'model': label, 'count': model.objects.count(), 'proposed_treatment': treatment})
        contribution_accounts = set(FundContribution.objects.values_list('receiving_account_id', flat=True))
        linked_agent_accounts = list(FinancialAccount.objects.filter(pk__in=contribution_accounts,
            legacy_cash_account__isnull=False).values_list('pk', flat=True))
        report = {
            'mode': 'READ_ONLY_NO_DELETION',
            'environment': settings.ENVIRONMENT,
            'database_engine': settings.DATABASES['default']['ENGINE'],
            'inventory': inventory,
            'contributions_by_currency_and_origin': list(FundContribution.objects.values('currency__code', 'origin').annotate(total=Sum('amount'))),
            'legacy_investments_by_currency': list(Investment.objects.values('currency__code').annotate(total=Sum('amount'))),
            'global_cash_by_currency': list(GlobalCashAccount.objects.values('currency__code').annotate(total=Sum('balance'))),
            'agent_cash_by_currency': list(CashAccount.objects.values('currency__code').annotate(total=Sum('balance'))),
            'contribution_receiving_accounts_linked_to_agent_cash': linked_agent_accounts,
            'notes': [
                'Ne pas additionner Investment et FundContribution : les deux peuvent représenter les mêmes apports.',
                'Les garanties et avances partenaires sont à distinguer du capital.',
                'Les comptes destinataires et lots des apports imposent une conservation ou une reprise documentée.',
                'La liste des candidats ne constitue pas un ordre de suppression.',
                'Sauvegarde restaurable, cible de déploiement et traitement du solde central requis avant exécution.',
            ],
        }
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, default=str))
