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
    FinancialAccount, AccountType, AccountNature, EntrySide,
    InternalTransfer, TransferStatus, JournalBatch
)
from apps.finance.services import prepare_batch, post_batch

@transaction.atomic
def run():
    admin = User.objects.filter(role=Role.ADMIN).first()
    usd = Currency.objects.get(code="USD")
    glodie_stk = Stakeholder.objects.get(name="Glodie Kongolo")

    print(f"Administrateur : {admin.email}")

    # 1. Compte source Vodacom M-Pesa
    source_acc = FinancialAccount.objects.get(code="MPESA-GLODIE-USD")
    print(f"Compte Source : {source_acc.code} ({source_acc.name}) | Solde actuel : {source_acc.cached_balance} USD")

    # 2. Compte destination Flash (FLASH-GLODIE-USD)
    dest_acc, dest_created = FinancialAccount.objects.get_or_create(
        code="FLASH-GLODIE-USD",
        defaults={
            "name": "Glodie Kongolo — Flash USD",
            "account_type": AccountType.DIGITAL,
            "nature": AccountNature.ASSET,
            "currency": usd,
            "responsible_user": admin,
            "economic_owner": glodie_stk,
            "is_active": True,
        }
    )
    print(f"Compte Destination : {dest_acc.code} ({dest_acc.name}) | Solde actuel : {dest_acc.cached_balance} USD | {'Créé' if dest_created else 'Existant'}")

    # 3. Compte de Transit USD
    transit_acc, _ = FinancialAccount.objects.get_or_create(
        code="TRANSIT-USD",
        defaults={
            "name": "Fonds en transit (USD)",
            "account_type": AccountType.TRANSIT,
            "nature": AccountNature.LIABILITY,
            "currency": usd,
            "responsible_user": admin,
            "is_active": True,
        }
    )

    amount = Decimal("500.00")
    client_key = "TRANSFER-VODACOM-FLASH-500-20260901"

    # Vérification si le transfert existe déjà
    transfer = InternalTransfer.objects.filter(client_key=client_key).first()
    if not transfer:
        now = timezone.now()
        transfer = InternalTransfer.objects.create(
            client_key=client_key,
            source=source_acc,
            destination=dest_acc,
            currency=usd,
            amount=amount,
            fee=Decimal("0.00"),
            note="Transfert interne de trésorerie Vodacom vers Flash (500 USD)",
            initiated_by=admin,
            initiated_at=now,
            received_by=admin,
            received_at=now,
            confirmed_by=admin,
            confirmed_at=now,
            status=TransferStatus.CONFIRMED,
        )

        # Batch 1 : Sortie de la source vers transit
        batch_init = prepare_batch(
            actor=admin,
            idempotency_key=f"BATCH-INIT-{client_key}",
            event_type="TRANSFER_INITIATE",
            effective_at=now,
            description=f"Transfert #{transfer.pk} : Sortie {source_acc.code} vers Transit",
            lines=[
                {"account": source_acc, "currency": usd, "side": EntrySide.CREDIT, "amount": amount, "memo": "Débit compte source Vodacom M-Pesa"},
                {"account": transit_acc, "currency": usd, "side": EntrySide.DEBIT, "amount": amount, "memo": "Fonds en transit vers Flash"},
            ],
            source_model="finance.InternalTransfer",
            source_id=transfer.pk,
        )
        post_batch(batch_id=batch_init.pk, actor=admin)
        transfer.initiation_batch = batch_init

        # Batch 2 : Entrée dans la destination depuis transit
        batch_settle = prepare_batch(
            actor=admin,
            idempotency_key=f"BATCH-SETTLE-{client_key}",
            event_type="TRANSFER_CONFIRM",
            effective_at=now,
            description=f"Transfert #{transfer.pk} : Arrivée Transit vers {dest_acc.code}",
            lines=[
                {"account": dest_acc, "currency": usd, "side": EntrySide.DEBIT, "amount": amount, "memo": "Crédit compte destination Flash"},
                {"account": transit_acc, "currency": usd, "side": EntrySide.CREDIT, "amount": amount, "memo": "Sortie des fonds en transit"},
            ],
            source_model="finance.InternalTransfer",
            source_id=transfer.pk,
        )
        post_batch(batch_id=batch_settle.pk, actor=admin)
        transfer.settlement_batch = batch_settle
        transfer.save(update_fields=["initiation_batch", "settlement_batch"])

        print(f"\n✓ Transfert #{transfer.pk} exécuté et confirmé avec succès !")
    else:
        print(f"\nTransfert déjà existant : #{transfer.pk}")

    source_acc.refresh_from_db()
    dest_acc.refresh_from_db()
    transit_acc.refresh_from_db()

    print("\n==========================================")
    print(f"Solde final Source (Vodacom M-Pesa) : {source_acc.cached_balance} USD")
    print(f"Solde final Destination (Flash)     : {dest_acc.cached_balance} USD")
    print(f"Solde Transit                       : {transit_acc.cached_balance} USD")
    print(f"Statut Transfert                    : {transfer.get_status_display()}")
    print("==========================================")

if __name__ == "__main__":
    run()
