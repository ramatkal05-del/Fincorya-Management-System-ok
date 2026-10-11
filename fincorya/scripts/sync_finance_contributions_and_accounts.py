import os
import sys
from datetime import date
from decimal import Decimal
import django

# Setup Django environment
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction
from django.utils import timezone
from apps.accounts.models import User, Role
from apps.pricing.models import Currency
from apps.cash.models import CashAccount, GlobalCashAccount
from apps.stakeholders.models import Stakeholder, StakeholderType
from apps.finance.models import (
    FinancialAccount, AccountType, AccountNature, EntrySide,
    FundContribution, FundOrigin, JournalBatch, LedgerEntry
)
from apps.finance.services import prepare_batch, post_batch

@transaction.atomic
def run():
    admin = User.objects.filter(role=Role.ADMIN).first()
    usd = Currency.objects.get(code="USD")
    global_cash_legacy = GlobalCashAccount.objects.get(currency=usd)

    print(f"Administrateur : {admin.email}")
    print(f"Caisse Globale Legacy : ID #{global_cash_legacy.pk} (Solde: {global_cash_legacy.balance} USD, Capital: {global_cash_legacy.capital} USD)\n")

    # 1. Compte Financier Trésorerie pour la Caisse Globale USD
    global_fin_account, _ = FinancialAccount.objects.get_or_create(
        code="GLOBAL-CASH-USD",
        defaults={
            "name": "Caisse Globale Trésorerie FINCORYA (USD)",
            "account_type": AccountType.GLOBAL_CASH,
            "nature": AccountNature.ASSET,
            "currency": usd,
            "responsible_user": admin,
            "legacy_global_account": global_cash_legacy,
            "is_active": True,
        }
    )
    if global_fin_account.legacy_global_account_id != global_cash_legacy.pk:
        global_fin_account.legacy_global_account = global_cash_legacy
        global_fin_account.save(update_fields=["legacy_global_account"])

    # 2. Comptes Financiers pour chaque Caisse Agent
    for cash_acc in CashAccount.objects.filter(currency=usd):
        agent = cash_acc.agent
        agent_fin_account, _ = FinancialAccount.objects.get_or_create(
            code=f"CASH-AGENT-{agent.pk}-USD",
            defaults={
                "name": f"Caisse Agent — {agent.get_full_name()} (USD)",
                "account_type": AccountType.AGENT_CASH,
                "nature": AccountNature.ASSET,
                "currency": usd,
                "responsible_user": agent,
                "legacy_cash_account": cash_acc,
                "is_active": True,
            }
        )
        if agent_fin_account.legacy_cash_account_id != cash_acc.pk:
            agent_fin_account.legacy_cash_account = cash_acc
            agent_fin_account.save(update_fields=["legacy_cash_account"])
        print(f"✓ Compte Caisse Agent synchronisé : {agent_fin_account.code} -> {agent.get_full_name()}")

    # 3. Les 6 Apports reçus à enregistrer dans FundContribution
    contributions_data = [
        {
            "stk_name": "Meschack Bukasa",
            "origin": FundOrigin.SHAREHOLDER,
            "amount": Decimal("7000.00"),
            "account_type": AccountType.CAPITAL,
            "nature": AccountNature.EQUITY,
            "desc": "Apport en capital actionnaire — Meschack Bukasa",
        },
        {
            "stk_name": "Glodie Kongolo",
            "origin": FundOrigin.SHAREHOLDER,
            "amount": Decimal("4320.00"),
            "account_type": AccountType.CAPITAL,
            "nature": AccountNature.EQUITY,
            "desc": "Apport en capital actionnaire — Glodie Kongolo",
        },
        {
            "stk_name": "Isaka",
            "origin": FundOrigin.SHAREHOLDER,
            "amount": Decimal("3400.00"),
            "account_type": AccountType.CAPITAL,
            "nature": AccountNature.EQUITY,
            "desc": "Apport en capital actionnaire — Isaka",
        },
        {
            "stk_name": "Mpoto Jenovic",
            "origin": FundOrigin.SHAREHOLDER,
            "amount": Decimal("1543.00"),
            "account_type": AccountType.CAPITAL,
            "nature": AccountNature.EQUITY,
            "desc": "Apport en capital actionnaire — Jenovic Mpoto",
        },
        {
            "stk_name": "Fantiny Pelenda",
            "origin": FundOrigin.PARTNER_GUARANTEE,
            "amount": Decimal("1500.00"),
            "account_type": AccountType.PARTNER_GUARANTEE,
            "nature": AccountNature.LIABILITY,
            "desc": "Garantie / Apport partenaire — Fantiny Pelenda",
        },
        {
            "stk_name": "Joseph Omari",
            "origin": FundOrigin.INVESTOR,
            "amount": Decimal("1050.00"),
            "account_type": AccountType.INVESTOR_FUNDS,
            "nature": AccountNature.LIABILITY,
            "desc": "Fonds investissement — Joseph Omari",
        },
    ]

    total_contributions = sum(c["amount"] for c in contributions_data)
    print(f"\nEnregistrement des {len(contributions_data)} apports dans le Registre Financier (Total: {total_contributions} USD)...\n")

    for item in contributions_data:
        stk = Stakeholder.objects.get(name=item["stk_name"])
        amount = item["amount"]
        client_key = f"CONTRIB-{stk.pk}-20260901"

        # Compte de passif/capitaux propres pour le contributeur
        equity_account, _ = FinancialAccount.objects.get_or_create(
            code=f"{item['account_type']}-USD-{stk.pk}",
            defaults={
                "name": item["desc"],
                "account_type": item["account_type"],
                "nature": item["nature"],
                "currency": usd,
                "economic_owner": stk,
                "responsible_user": admin,
                "is_active": True,
            }
        )

        # Batch comptable pour l'apport
        idempotency_key = f"BATCH-{client_key}"
        batch = JournalBatch.objects.filter(idempotency_key=idempotency_key).first()
        if not batch:
            batch = prepare_batch(
                actor=admin,
                idempotency_key=idempotency_key,
                event_type="CONTRIBUTION",
                effective_at=timezone.now(),
                description=item["desc"],
                lines=[
                    {"account": global_fin_account, "currency": usd, "side": EntrySide.DEBIT, "amount": amount, "memo": f"Versement apport {stk.name}"},
                    {"account": equity_account, "currency": usd, "side": EntrySide.CREDIT, "amount": amount, "memo": f"Crédit compte {stk.name}"},
                ],
                source_model="finance.FundContribution",
                source_id=client_key,
            )
            post_batch(batch_id=batch.pk, actor=admin)

        # Enregistrement dans FundContribution
        contrib, contrib_created = FundContribution.objects.update_or_create(
            client_key=client_key,
            defaults={
                "origin": item["origin"],
                "stakeholder": stk,
                "amount": amount,
                "currency": usd,
                "received_on": date(2026, 9, 1),
                "receiving_account": global_fin_account,
                "from_opening_balances": False,
                "external_reference": f"VIR-CAPITAL-{stk.pk:02d}",
                "note": item["desc"],
                "batch": batch,
                "created_by": admin,
            }
        )
        print(f"✓ Apport enregistré : {contrib.stakeholder.name:<18} | {contrib.get_origin_display():<22} | {contrib.amount:>8} USD | Vers: {contrib.receiving_account.code} | Écriture #{batch.pk}")

    global_fin_account.refresh_from_db()
    print(f"\n==========================================")
    print(f"Solde compte Trésorerie Caisse Globale : {global_fin_account.cached_balance} USD")
    print(f"Total des apports affichés au Registre : {FundContribution.objects.count()} apports pour {sum(c.amount for c in FundContribution.objects.all())} USD")
    print(f"==========================================")

if __name__ == "__main__":
    run()
