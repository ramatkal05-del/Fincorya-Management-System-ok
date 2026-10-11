import os
import sys
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
from apps.stakeholders.models import Stakeholder
from apps.finance.models import (
    FinancialAccount, AccountType, AccountNature, EntrySide, JournalBatch, LedgerEntry
)
from apps.finance.services import prepare_batch, post_batch

@transaction.atomic
def run():
    admin = User.objects.filter(role=Role.ADMIN).first()
    usd = Currency.objects.get(code="USD")
    glodie_stk = Stakeholder.objects.get(name="Glodie Kongolo")

    print(f"Administrateur : {admin.email}")
    print(f"Partie prenante : {glodie_stk.name} ({glodie_stk.get_type_display()})\n")

    # Contrepartie Capital pour Glodie Kongolo
    capital_account, _ = FinancialAccount.objects.get_or_create(
        code=f"CAPITAL-USD-{glodie_stk.pk}",
        defaults={
            "name": f"Capital apporté — {glodie_stk.name}",
            "account_type": AccountType.CAPITAL,
            "nature": AccountNature.EQUITY,
            "currency": usd,
            "economic_owner": glodie_stk,
            "responsible_user": admin,
        }
    )

    accounts_data = [
        {
            "code": "MPESA-GLODIE-USD",
            "name": "Glodie Kongolo — M-Pesa USD",
            "type": AccountType.DIGITAL,
            "amount": Decimal("1576.00"),
        },
        {
            "code": "PAYPAL-GLODIE-USD",
            "name": "Glodie Kongolo — PayPal USD",
            "type": AccountType.DIGITAL,
            "amount": Decimal("350.00"),
        },
        {
            "code": "CASH-GLODIE-USD",
            "name": "Glodie Kongolo — Caisse physique USD",
            "type": AccountType.AGENT_CASH,
            "amount": Decimal("1540.00"),
        },
        {
            "code": "AIRTEL-GLODIE-USD",
            "name": "Glodie Kongolo — Airtel Money USD",
            "type": AccountType.DIGITAL,
            "amount": Decimal("830.00"),
        },
    ]

    total_amount = sum(acc["amount"] for acc in accounts_data)
    print(f"Création et initialisation des {len(accounts_data)} comptes (Total: {total_amount} USD)...\n")

    for acc_info in accounts_data:
        account, created = FinancialAccount.objects.get_or_create(
            code=acc_info["code"],
            defaults={
                "name": acc_info["name"],
                "account_type": acc_info["type"],
                "nature": AccountNature.ASSET,
                "currency": usd,
                "economic_owner": glodie_stk,
                "responsible_user": admin,
                "is_active": True,
            }
        )

        amount = acc_info["amount"]
        idempotency_key = f"INIT-ACCOUNT-{account.code}"

        # Écriture comptable pour initialiser le solde
        existing_batch = JournalBatch.objects.filter(idempotency_key=idempotency_key).first()
        if not existing_batch:
            batch = prepare_batch(
                actor=admin,
                idempotency_key=idempotency_key,
                event_type="ACCOUNT_OPENING",
                effective_at=timezone.now(),
                description=f"Ouverture et versement initial {account.code}",
                lines=[
                    {"account": account, "currency": usd, "side": EntrySide.DEBIT, "amount": amount, "memo": f"Solde initial {account.name}"},
                    {"account": capital_account, "currency": usd, "side": EntrySide.CREDIT, "amount": amount, "memo": f"Contrepartie capital {glodie_stk.name}"},
                ],
                source_model="finance.FinancialAccount",
                source_id=account.pk,
            )
            post_batch(batch_id=batch.pk, actor=admin)

        account.refresh_from_db()
        print(f"✓ Compte: {account.code:<20} | Type: {account.get_account_type_display():<24} | Solde: {account.cached_balance:>10} USD | Propriétaire: {account.economic_owner.name}")

    capital_account.refresh_from_db()
    print(f"\n==========================================")
    print(f"Total actif enregistré sur les comptes : {total_amount} USD")
    print(f"Solde total du compte de capital Glodie : {capital_account.cached_balance} USD")
    print(f"==========================================")

if __name__ == "__main__":
    run()
