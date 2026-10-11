import os
import sys
from decimal import Decimal
from datetime import datetime, date, timezone as dt_timezone
import django

# Setup Django environment
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction
from django.utils import timezone
from apps.accounts.models import User, Role
from apps.pricing.models import Currency, TariffSchedule
from apps.pricing.services import lookup_fee
from apps.cash.models import CashAccount, GlobalCashAccount
from apps.cash.services import allocate_cash
from apps.operations.models import Operation, OperationType, FeeMode
from apps.operations.services import create_sent_transfer, create_withdrawal
from apps.stakeholders.models import Stakeholder
from apps.finance.models import (
    FinancialAccount, AccountType, AccountNature, EntrySide,
    InternalTransfer, TransferStatus, JournalBatch
)
from apps.finance.locking import save_internal
from apps.profits.models import ProfitPeriod
from apps.profits.services import calculate_profit_period, allocate_individual_distributions

RAW_TRANSACTIONS_W3 = [
    # 1: VODACOM, RETRAIT 44 $, VICTORIA, COMM 8 $, LEFKOSA
    {"id": 1, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("44.00"), "fee": Decimal("8.00"), "agent": "VICTORIA", "city": "LEFKOSA"},
    # 2: VODACOM, ENVOI 310 $, RUTH, COMM 26 $, GIRNE
    {"id": 2, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("310.00"), "fee": Decimal("26.00"), "agent": "RUTH", "city": "GIRNE"},
    # 3: AIRTEL, ENVOI 40 $, VICTORIA, COMM 5 $, LEFKOSA
    {"id": 3, "service": "AIRTEL_MONEY", "type": "ENVOI", "amount": Decimal("40.00"), "fee": Decimal("5.00"), "agent": "VICTORIA", "city": "LEFKOSA"},
    # 4: VODACOM, ENVOI 20 $, CHRIST, COMM 5 $, GIRNE
    {"id": 4, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("20.00"), "fee": Decimal("5.00"), "agent": "CHRIST", "city": "GIRNE"},
    # 5: VODACOM, ENVOI 20 $, VICTORIA, COMM 5 $, LEFKOSA
    {"id": 5, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("20.00"), "fee": Decimal("5.00"), "agent": "VICTORIA", "city": "LEFKOSA"},
    # 6: VODACOM, ENVOI 30 $, VICTORIA, COMM 5 $, LEFKOSA
    {"id": 6, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("30.00"), "fee": Decimal("5.00"), "agent": "VICTORIA", "city": "LEFKOSA"},
    # 7: PAYPAL, ENVOI 34 $, DAV, COMM 5 $, LEFKOSA
    {"id": 7, "service": "PAYPAL", "type": "ENVOI", "amount": Decimal("34.00"), "fee": Decimal("5.00"), "agent": "DAV", "city": "LEFKOSA"},
    # 8: VODACOM, RETRAIT 87 $, RUTH, COMM 8 $, GIRNE
    {"id": 8, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("87.00"), "fee": Decimal("8.00"), "agent": "RUTH", "city": "GIRNE"},
    # 9: ORANGE, ENVOI 185 $, CHRIS, COMM 15 $, GIRNE
    {"id": 9, "service": "ORANGE_MONEY", "type": "ENVOI", "amount": Decimal("185.00"), "fee": Decimal("15.00"), "agent": "CHRIST", "city": "GIRNE"},
    # 10: VODACOM, ENVOI 44 $, RUTH, COMM 5 $, GIRNE
    {"id": 10, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("44.00"), "fee": Decimal("5.00"), "agent": "RUTH", "city": "GIRNE"},
    # 11: VODACOM, RETRAIT 115 $, VICTORIA, COMM 15 $, LEFKOSA
    {"id": 11, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("115.00"), "fee": Decimal("15.00"), "agent": "VICTORIA", "city": "LEFKOSA"},
    # 12: AIRTEL, RETRAIT 42 $, RUTH, COMM 8 $, GIRNE
    {"id": 12, "service": "AIRTEL_MONEY", "type": "RETRAIT", "amount": Decimal("42.00"), "fee": Decimal("8.00"), "agent": "RUTH", "city": "GIRNE"},
    # 13: VODACOM, RETRAIT 115 $, RUTH, COMM 15 $, GIRNE
    {"id": 13, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("115.00"), "fee": Decimal("15.00"), "agent": "RUTH", "city": "GIRNE"},
    # 14: ORANGE, ENVOI 20 $, VICTORIA, COMM 5 $, LEFKOSA
    {"id": 14, "service": "ORANGE_MONEY", "type": "ENVOI", "amount": Decimal("20.00"), "fee": Decimal("5.00"), "agent": "VICTORIA", "city": "LEFKOSA"},
    # 15: VODACOM, RETRAIT 25 $, DAV, COMM 5 $, LEFKOSA
    {"id": 15, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("25.00"), "fee": Decimal("5.00"), "agent": "DAV", "city": "LEFKOSA"},
    # 16: AIRTEL, RETRAIT 600 $, RUTH, COMM 30 $, GIRNE
    {"id": 16, "service": "AIRTEL_MONEY", "type": "RETRAIT", "amount": Decimal("600.00"), "fee": Decimal("30.00"), "agent": "RUTH", "city": "GIRNE"},
    # 17: AIRTEL, RETRAIT 200 $, VICTORIA, COMM 15 $, LEFKOSA
    {"id": 17, "service": "AIRTEL_MONEY", "type": "RETRAIT", "amount": Decimal("200.00"), "fee": Decimal("15.00"), "agent": "VICTORIA", "city": "LEFKOSA"},
    # 18: AIRTEL, ENVOI 103 $, CHRIS, COMM 8 $, GIRNE
    {"id": 18, "service": "AIRTEL_MONEY", "type": "ENVOI", "amount": Decimal("103.00"), "fee": Decimal("8.00"), "agent": "CHRIST", "city": "GIRNE"},
    # 19: AIRTEL, ENVOI 53 $, CHRIS, COMM 8 $, GIRNE
    {"id": 19, "service": "AIRTEL_MONEY", "type": "ENVOI", "amount": Decimal("53.00"), "fee": Decimal("8.00"), "agent": "CHRIST", "city": "GIRNE"},
    # 20: VODACOM, ENVOI 20 $, VICTORIA, COMM 5 $, LEFKOSA
    {"id": 20, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("20.00"), "fee": Decimal("5.00"), "agent": "VICTORIA", "city": "LEFKOSA"},
    # 21: VODACOM, ENVOI 50 $, RUTH, COMM 8 $, GIRNE
    {"id": 21, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("50.00"), "fee": Decimal("8.00"), "agent": "RUTH", "city": "GIRNE"},
    # 22: VODACOM, RETRAIT 72 $, DAV, COMM 8 $, LEFKOSA
    {"id": 22, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("72.00"), "fee": Decimal("8.00"), "agent": "DAV", "city": "LEFKOSA"},
    # 23: VODACOM, RETRAIT 94 $, VICTORIA, COMM 8 $, LEFKOSA
    {"id": 23, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("94.00"), "fee": Decimal("8.00"), "agent": "VICTORIA", "city": "LEFKOSA"},
    # 24: VODACOM, RETRAIT 300 $, RUTH, COMM 20 $, GIRNE
    {"id": 24, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("300.00"), "fee": Decimal("20.00"), "agent": "RUTH", "city": "GIRNE"},
    # 25: VODACOM, ENVOI 20 $, CHRIS, COMM 5 $, GIRNE
    {"id": 25, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("20.00"), "fee": Decimal("5.00"), "agent": "CHRIST", "city": "GIRNE"},
    # 26: AIRTEL, ENVOI 25 $, CHRIS, COMM 5 $, GIRNE
    {"id": 26, "service": "AIRTEL_MONEY", "type": "ENVOI", "amount": Decimal("25.00"), "fee": Decimal("5.00"), "agent": "CHRIST", "city": "GIRNE"},
    # 27: VODACOM, ENVOI 20 $, RUTH, COMM 5 $, GIRNE
    {"id": 27, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("20.00"), "fee": Decimal("5.00"), "agent": "RUTH", "city": "GIRNE"},
    # 28: VODACOM, RETRAIT 100 $, CHRIS, COMM 8 $, GIRNE
    {"id": 28, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("100.00"), "fee": Decimal("8.00"), "agent": "CHRIST", "city": "GIRNE"},
]

AGENT_EMAILS = {
    "DAV": "david.matamba@fincorya.local",
    "VICTORIA": "victorine.tshonda@fincorya.local",
    "RUTH": "ruth.ngomo@fincorya.local",
    "CHRIS": "christ.kapia@fincorya.local",
    "CHRIST": "christ.kapia@fincorya.local",
}

@transaction.atomic
def run():
    admin = User.objects.filter(role=Role.ADMIN).first()
    usd = Currency.objects.get(code="USD")
    schedule = TariffSchedule.objects.get(name="FINCORYA PRD V1")
    global_account = GlobalCashAccount.objects.get(currency=usd)
    glodie_stk = Stakeholder.objects.get(name="Glodie Kongolo")

    print("=== 1. EXÉCUTION DU TRANSFERT INTERNE DE LA SEMAINE (500 USD) ===")
    source_acc = FinancialAccount.objects.get(code="CASH-GLODIE-USD")
    dest_acc, dest_created = FinancialAccount.objects.get_or_create(
        code="ORANGE-GLODIE-USD",
        defaults={
            "name": "Glodie Kongolo — Orange Money USD",
            "account_type": AccountType.MOBILE_MONEY,
            "nature": AccountNature.ASSET,
            "currency": usd,
            "responsible_user": admin,
            "economic_owner": glodie_stk,
            "is_active": True,
        }
    )

    transfer_amount = Decimal("500.00")
    client_key = "TRANSFER-CASH-ORANGE-500-20260914"

    transfer = InternalTransfer.objects.filter(client_key=client_key).first()
    if not transfer:
        transfer = InternalTransfer.objects.create(
            client_key=client_key,
            source=source_acc,
            destination=dest_acc,
            currency=usd,
            amount=transfer_amount,
            fee=Decimal("0.00"),
            note="Transfert interne : 500$ Cash du compte de Glodie vers compte Orange",
            initiated_by=admin,
            initiated_at=timezone.now(),
            received_by=admin,
            received_at=timezone.now(),
            confirmed_by=admin,
            confirmed_at=timezone.now(),
            status=TransferStatus.CONFIRMED,
        )
        source_acc.cached_balance -= transfer_amount
        save_internal(source_acc, update_fields=["cached_balance"])
        dest_acc.cached_balance += transfer_amount
        save_internal(dest_acc, update_fields=["cached_balance"])
        print(f"✓ Transfert interne #{transfer.pk} exécuté : 500 USD transférés de {source_acc.code} vers {dest_acc.code}")
        print(f"  • Solde {source_acc.code} : {source_acc.cached_balance} USD")
        print(f"  • Solde {dest_acc.code} : {dest_acc.cached_balance} USD")
    else:
        print(f"Transfert déjà enregistré : #{transfer.pk}")

    print("\n=== 2. ENREGISTREMENT DES 28 TRANSACTIONS DE LA SEMAINE 3 (14/09/2026 - 20/09/2026) ===")

    agents = {code: User.objects.get(email=email) for code, email in AGENT_EMAILS.items()}
    created_ops = []

    for i, tx in enumerate(RAW_TRANSACTIONS_W3):
        tx_id = tx["id"]
        agent = agents[tx["agent"]]
        service = tx["service"]
        amount = tx["amount"]
        fee = tx["fee"]
        city = tx["city"]
        op_type = tx["type"]

        cash_account = CashAccount.objects.select_for_update().get(agent=agent, currency=usd)

        # Calcul auto des frais
        auto_fee = lookup_fee(schedule, amount)
        manual_fee = fee if fee != auto_fee else None

        # Vérification liquidité pour retraits
        if op_type == "RETRAIT":
            payout_needed = amount - fee
            if cash_account.balance < payout_needed:
                needed = (payout_needed - cash_account.balance + Decimal("100.00")).quantize(Decimal("100.00"))
                print(f"  [Trésorerie] Réapprovisionnement de {needed} USD pour la caisse {agent.get_full_name()}...")
                allocate_cash(
                    account_id=cash_account.pk,
                    amount=needed,
                    allocated_by=admin,
                    note=f"Réapprovisionnement caisse opération W3 #{tx_id}"
                )
                cash_account.refresh_from_db()

        idempotency_key = f"OP-TX-{tx_id}-2026-09-W3"

        if op_type == "ENVOI":
            op = create_sent_transfer(
                agent=agent,
                account_id=cash_account.pk,
                amount=amount,
                tariff_schedule=schedule,
                manual_fee=manual_fee,
                service=service,
                customer_identifier=city,
                customer_name="Client Agence",
                note=f"Transaction Semaine 3 #{tx_id} - Réseau: {service} - Ville: {city}",
                idempotency_key=idempotency_key,
                fee_mode=FeeMode.ADDED,
            )
        else: # RETRAIT
            op = create_withdrawal(
                agent=agent,
                account_id=cash_account.pk,
                amount=amount,
                tariff_schedule=schedule,
                manual_fee=manual_fee,
                service=service,
                customer_identifier=city,
                customer_name="Client Agence",
                note=f"Transaction Semaine 3 #{tx_id} - Réseau: {service} - Ville: {city}",
                idempotency_key=idempotency_key,
                fee_mode=FeeMode.DEDUCTED,
            )

        # Date dans la Semaine 3 (14/09/2026 au 19/09/2026)
        op_time = datetime(2026, 9, 14 + (i % 6), 9 + (i % 8), (i * 4) % 60, tzinfo=dt_timezone.utc)
        Operation.objects.filter(pk=op.pk).update(created_at=op_time)
        op.refresh_from_db()

        cash_account.refresh_from_db()
        created_ops.append(op)
        print(f"✓ Op #{tx_id:02d} | {op.get_type_display():<17} | {amount:>7} USD | Comm: {fee:>3} USD | Agent: {tx['agent']:<8} | Solde caisse: {cash_account.balance:>8} USD | Date: {op.created_at.strftime('%d/%m/%Y %H:%M')}")

    print(f"\n==========================================")
    print(f"Total des 28 opérations enregistrées avec succès.")
    print(f"Volume Envois : {sum(op.amount for op in created_ops if op.type == OperationType.SENT_TRANSFER)} USD")
    print(f"Volume Retraits : {sum(op.amount for op in created_ops if op.type == OperationType.WITHDRAWAL)} USD")
    print(f"Commissions totales collectées : {sum(op.fee for op in created_ops)} USD")
    print(f"==========================================")

    # 3. Recalcul de la période de bénéfice Semaine 3
    pp3 = ProfitPeriod.objects.filter(start_date=date(2026, 9, 14)).first()
    if pp3:
        pp3.status = "DRAFT"
        pp3.save(update_fields=["status"])
        calculate_profit_period(period_id=pp3.pk, actor=admin)
        allocate_individual_distributions(period_id=pp3.pk, actor=admin)
        pp3.refresh_from_db()

        print(f"\n=== BÉNÉFICES & RÉPARTITION SEMAINE 3 ===")
        print(f"• Commissions brutes collectées : {pp3.gross_fees} USD")
        print(f"• Charges d'exploitation : {pp3.deductible_expenses} USD")
        print(f"• Bénéfice net de la semaine 3 : {pp3.net_profit} USD\n")

        for a in pp3.allocations.all():
            print(f"Enveloppe {a.bucket} ({a.percentage}% = {a.amount} USD) :")
            for d in a.distributions.all():
                print(f"  * {d.stakeholder.name}: {d.amount} USD")

    print("\n=== SOLDES ACTUELS DES CAISSES AGENTS ===")
    for code, agent in [("DAV", agents["DAV"]), ("VICTORIA", agents["VICTORIA"]), ("RUTH", agents["RUTH"]), ("CHRIST", agents["CHRIST"])]:
        ca = CashAccount.objects.get(agent=agent, currency=usd)
        print(f"Agent {agent.get_full_name():<25} ({code}) -> Solde caisse : {ca.balance} USD")

if __name__ == "__main__":
    run()
