import os
import sys
from decimal import Decimal
import django

# Setup Django environment
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction
from apps.accounts.models import User, Role
from apps.pricing.models import Currency, TariffSchedule
from apps.pricing.services import lookup_fee
from apps.cash.models import CashAccount, GlobalCashAccount
from apps.cash.services import allocate_cash
from apps.stakeholders.models import Stakeholder, PartnerOperation
from apps.finance.models import PartnerGuarantee
from apps.finance.funds import set_partner_guarantee
from apps.operations.models import Operation, OperationType, FeeMode
from apps.operations.services import create_sent_transfer, create_withdrawal

@transaction.atomic
def run():
    admin = User.objects.filter(role=Role.ADMIN).first()
    usd = Currency.objects.get(code="USD")
    schedule = TariffSchedule.objects.get(name="FINCORYA PRD V1")
    global_account = GlobalCashAccount.objects.get(currency=usd)
    fantiny_stk = Stakeholder.objects.get(name="Fantiny Pelenda")

    print(f"Administrateur exécutant : {admin.email}")
    print(f"Partenaire bénéficiaire : {fantiny_stk.name} ({fantiny_stk.get_type_display()})\n")

    # 1. S'assurer d'une garantie et d'un plafond partenaire actif pour Fantiny
    guarantee, _ = PartnerGuarantee.objects.get_or_create(
        stakeholder=fantiny_stk,
        defaults={
            "currency": usd,
            "per_operation_ceiling": Decimal("1000.00"),
            "is_active": True,
            "updated_by": admin,
        }
    )
    if not guarantee.is_active:
        guarantee.is_active = True
        guarantee.save(update_fields=["is_active"])
    print(f"Garantie partenaire active : Plafond {guarantee.per_operation_ceiling} USD / opération")

    # 2. Caisse Admin pour exécuter les opérations
    admin_cash, created = CashAccount.objects.get_or_create(
        agent=admin,
        currency=usd,
        defaults={
            "global_account": global_account,
            "is_active": True,
            "allocated_by": admin,
        }
    )
    if not admin_cash.is_active or admin_cash.global_account_id != global_account.pk:
        admin_cash.is_active = True
        admin_cash.global_account = global_account
        admin_cash.allocated_by = admin
        admin_cash.save()

    # Si la caisse admin a besoin d'un fonds de roulement pour les retraits (250 + 500 + 62 = 812 USD)
    if admin_cash.balance < Decimal("1000.00"):
        needed = Decimal("1000.00") - admin_cash.balance
        if global_account.balance >= needed:
            allocate_cash(
                account_id=admin_cash.pk,
                amount=needed,
                allocated_by=admin,
                note="Fonds de roulement caisse administration pour opérations partenaire"
            )
            admin_cash.refresh_from_db()
            print(f"Caisse Admin approvisionnée : Solde = {admin_cash.balance} USD")

    # 3. Les 6 opérations pour le compte de Fantiny
    # RESEAU | ENVOI | RETRAIT
    # 1. VODACOM : ENVOI 200 $
    # 2. VODACOM : ENVOI 70 $
    # 3. VODACOM : ENVOI 94 $
    # 4. AIRTEL : RETRAIT 250 $
    # 5. VODACOM : RETRAIT 500 $
    # 6. VODACOM : RETRAIT 62 $

    fantiny_ops = [
        {"id": 1, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("200.00")},
        {"id": 2, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("70.00")},
        {"id": 3, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("94.00")},
        {"id": 4, "service": "AIRTEL_MONEY", "type": "RETRAIT", "amount": Decimal("250.00")},
        {"id": 5, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("500.00")},
        {"id": 6, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("62.00")},
    ]

    print(f"\nExécution des {len(fantiny_ops)} opérations pour le compte de Fantiny Pelenda...\n")

    created_ops = []

    for item in fantiny_ops:
        op_id = item["id"]
        service = item["service"]
        amount = item["amount"]
        op_type = item["type"]
        fee = lookup_fee(schedule, amount)

        idempotency_key = f"OP-FANTINY-ADMIN-{op_id}-20260901"

        if op_type == "ENVOI":
            op = create_sent_transfer(
                agent=admin,
                account_id=admin_cash.pk,
                amount=amount,
                tariff_schedule=schedule,
                service=service,
                customer_identifier="FANTINY",
                customer_name=fantiny_stk.name,
                note=f"Opération #{op_id} pour Fantiny Pelenda (Réseau {service})",
                idempotency_key=idempotency_key,
                stakeholder=fantiny_stk,
                commission_owner_confirmed=True,
                fee_mode=FeeMode.ADDED,
            )
        else: # RETRAIT
            op = create_withdrawal(
                agent=admin,
                account_id=admin_cash.pk,
                amount=amount,
                tariff_schedule=schedule,
                service=service,
                customer_identifier="FANTINY",
                customer_name=fantiny_stk.name,
                note=f"Opération #{op_id} pour Fantiny Pelenda (Réseau {service})",
                idempotency_key=idempotency_key,
                stakeholder=fantiny_stk,
                commission_owner_confirmed=True,
                fee_mode=FeeMode.DEDUCTED,
            )

        admin_cash.refresh_from_db()
        created_ops.append(op)
        print(f"✓ Op #{op_id} | {op.get_type_display():<17} | {op.service:<14} | {amount:>6} USD | Comm: {op.fee:>3} USD | Solde Caisse Admin: {admin_cash.balance:>7} USD | Ref: {op.reference}")

    print("\n==========================================")
    print(f"Total des 6 opérations de Fantiny exécutées avec succès.")
    print(f"Volume Envois   : {sum(op.amount for op in created_ops if op.type == OperationType.SENT_TRANSFER)} USD")
    print(f"Volume Retraits : {sum(op.amount for op in created_ops if op.type == OperationType.WITHDRAWAL)} USD")
    print(f"Commissions totales générées : {sum(op.fee for op in created_ops)} USD")
    print(f"Solde Caisse Admin FINCORYA : {admin_cash.balance} USD")
    print("==========================================")

if __name__ == "__main__":
    run()
