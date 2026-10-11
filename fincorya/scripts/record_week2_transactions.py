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

RAW_TRANSACTIONS_W2 = [
    # 1: AIRTEL, ENVOI 20 $, CHRIST, COMM 5 $, GIRNE, JENOVIC
    {"id": 1, "service": "AIRTEL_MONEY", "type": "ENVOI", "amount": Decimal("20.00"), "fee": Decimal("5.00"), "agent": "CHRIST", "city": "GIRNE", "compte": "JENOVIC"},
    # 2: AIRTEL, ENVOI 13 $, RUTH, COMM 5 $, GIRNE, JENOVIC
    {"id": 2, "service": "AIRTEL_MONEY", "type": "ENVOI", "amount": Decimal("13.00"), "fee": Decimal("5.00"), "agent": "RUTH", "city": "GIRNE", "compte": "JENOVIC"},
    # 3: AIRTEL, ENVOI 25 $, VICTORIA, COMM 5 $, LEFKOSA, JENOVIC
    {"id": 3, "service": "AIRTEL_MONEY", "type": "ENVOI", "amount": Decimal("25.00"), "fee": Decimal("5.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 4: AIRTEL, ENVOI 50 $, VICTORIA, COMM 8 $, LEFKOSA, JENOVIC
    {"id": 4, "service": "AIRTEL_MONEY", "type": "ENVOI", "amount": Decimal("50.00"), "fee": Decimal("8.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 5: AIRTEL, ENVOI 70 $, RUTH, COMM 8 $, GIRNE, JENOVIC
    {"id": 5, "service": "AIRTEL_MONEY", "type": "ENVOI", "amount": Decimal("70.00"), "fee": Decimal("8.00"), "agent": "RUTH", "city": "GIRNE", "compte": "JENOVIC"},
    # 6: ORANGE, ENVOI 50 $, CHRIST, COMM 8 $, GIRNE, JENOVIC
    {"id": 6, "service": "ORANGE_MONEY", "type": "ENVOI", "amount": Decimal("50.00"), "fee": Decimal("8.00"), "agent": "CHRIST", "city": "GIRNE", "compte": "JENOVIC"},
    # 7: VODACOM, RETRAIT 47 $, CHRIST, COMM 8 $, GIRNE, JENOVIC
    {"id": 7, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("47.00"), "fee": Decimal("8.00"), "agent": "CHRIST", "city": "GIRNE", "compte": "JENOVIC"},
    # 8: ORANGE, ENVOI 45 $, VICTORIA, COMM 5 $, LEFKOSA, JENOVIC
    {"id": 8, "service": "ORANGE_MONEY", "type": "ENVOI", "amount": Decimal("45.00"), "fee": Decimal("5.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 9: AIRTEL, RETRAIT 300 $, VICTORIA, COMM 20 $, LEFKOSA, JENOVIC
    {"id": 9, "service": "AIRTEL_MONEY", "type": "RETRAIT", "amount": Decimal("300.00"), "fee": Decimal("20.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 10: VODACOM, RETRAIT 860 $, DAV, COMM 40 $, LEFKOSA, GLODIE
    {"id": 10, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("860.00"), "fee": Decimal("40.00"), "agent": "DAV", "city": "LEFKOSA", "compte": "GLODIE"},
    # 11: AIRTEL, ENVOI 102 $, VICTORIA, COMM 8 $, LEFKOSA, JENOVIC
    {"id": 11, "service": "AIRTEL_MONEY", "type": "ENVOI", "amount": Decimal("102.00"), "fee": Decimal("8.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 12: VODACOM, ENVOI 20 $, CHRIST, COMM 5 $, GIRNE, JENOVIC
    {"id": 12, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("20.00"), "fee": Decimal("5.00"), "agent": "CHRIST", "city": "GIRNE", "compte": "JENOVIC"},
    # 13: VODACOM, ENVOI 50 $, CHRIST, COMM 8 $, GIRNE, JENOVIC
    {"id": 13, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("50.00"), "fee": Decimal("8.00"), "agent": "CHRIST", "city": "GIRNE", "compte": "JENOVIC"},
    # 14: VODACOM, ENVOI 30 $, CHRIST, COMM 5 $, GIRNE, JENOVIC
    {"id": 14, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("30.00"), "fee": Decimal("5.00"), "agent": "CHRIST", "city": "GIRNE", "compte": "JENOVIC"},
    # 15: VODACOM, ENVOI 22 $, RUTH, COMM 5 $, GIRNE, JENOVIC
    {"id": 15, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("22.00"), "fee": Decimal("5.00"), "agent": "RUTH", "city": "GIRNE", "compte": "JENOVIC"},
    # 16: VODACOM, ENVOI 72 $, VICTORIA, COMM 8 $, LEFKOSA, JENOVIC
    {"id": 16, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("72.00"), "fee": Decimal("8.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 17: AIRTEL, ENVOI 40 $, VICTORIA, COMM 5 $, LEFKOSA, JENOVIC
    {"id": 17, "service": "AIRTEL_MONEY", "type": "ENVOI", "amount": Decimal("40.00"), "fee": Decimal("5.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 18: VODACOM, RETRAIT 97 $, DAV, COMM 8 $, LEFKOSA, GLODIE
    {"id": 18, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("97.00"), "fee": Decimal("8.00"), "agent": "DAV", "city": "LEFKOSA", "compte": "GLODIE"},
    # 19: AIRTEL, ENVOI 102 $, CHRIST, COMM 15 $, GIRNE, JENOVIC
    {"id": 19, "service": "AIRTEL_MONEY", "type": "ENVOI", "amount": Decimal("102.00"), "fee": Decimal("15.00"), "agent": "CHRIST", "city": "GIRNE", "compte": "JENOVIC"},
    # 20: VODACOM, RETRAIT 94 $, CHRIST, COMM 8 $, GIRNE, JENOVIC
    {"id": 20, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("94.00"), "fee": Decimal("8.00"), "agent": "CHRIST", "city": "GIRNE", "compte": "JENOVIC"},
    # 21: VODACOM, ENVOI 48 $, DAV, COMM 8 $, LEFKOSA, GLODIE
    {"id": 21, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("48.00"), "fee": Decimal("8.00"), "agent": "DAV", "city": "LEFKOSA", "compte": "GLODIE"},
    # 22: VODACOM, ENVOI 150 $, VICTORIA, COMM 15 $, LEFKOSA, JENOVIC
    {"id": 22, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("150.00"), "fee": Decimal("15.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 23: ORANGE, ENVOI 110 $, RUTH, COMM 15 $, GIRNE, JENOVIC
    {"id": 23, "service": "ORANGE_MONEY", "type": "ENVOI", "amount": Decimal("110.00"), "fee": Decimal("15.00"), "agent": "RUTH", "city": "GIRNE", "compte": "JENOVIC"},
    # 24: ORANGE, ENVOI 25 $, CHRIST, COMM 5 $, GIRNE, JENOVIC
    {"id": 24, "service": "ORANGE_MONEY", "type": "ENVOI", "amount": Decimal("25.00"), "fee": Decimal("5.00"), "agent": "CHRIST", "city": "GIRNE", "compte": "JENOVIC"},
    # 25: VODACOM, ENVOI 15 $, RUTH, COMM 5 $, GIRNE, JENOVIC
    {"id": 25, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("15.00"), "fee": Decimal("5.00"), "agent": "RUTH", "city": "GIRNE", "compte": "JENOVIC"},
    # 26: AIRTEL, ENVOI 20 $, VICTORIA, COMM 5 $, LEFKOSA, JENOVIC
    {"id": 26, "service": "AIRTEL_MONEY", "type": "ENVOI", "amount": Decimal("20.00"), "fee": Decimal("5.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 27: AIRTEL, ENVOI 15 $, DAV, COMM 5 $, LEFKOSA, JENOVIC
    {"id": 27, "service": "AIRTEL_MONEY", "type": "ENVOI", "amount": Decimal("15.00"), "fee": Decimal("5.00"), "agent": "DAV", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 28: VODACOM, RETRAIT 62 $, RUTH, COMM 8 $, GIRNE, JENOVIC
    {"id": 28, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("62.00"), "fee": Decimal("8.00"), "agent": "RUTH", "city": "GIRNE", "compte": "JENOVIC"},
    # 29: VODACOM, ENVOI 400 $, VICTORIA, COMM 26 $, LEFKOSA, JENOVIC
    {"id": 29, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("400.00"), "fee": Decimal("26.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 30: VODACOM, ENVOI 100 $, VICTORIA, COMM 8 $, LEFKOSA, GLODIE
    {"id": 30, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("100.00"), "fee": Decimal("8.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "GLODIE"},
    # 31: AIRTEL, RETRAIT 240 $, VICTORIA, COMM 20 $, LEFKOSA, JENOVIC
    {"id": 31, "service": "AIRTEL_MONEY", "type": "RETRAIT", "amount": Decimal("240.00"), "fee": Decimal("20.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 32: VODACOM, ENVOI 25 $, RUTH, COMM 5 $, GIRNE, JENOVIC
    {"id": 32, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("25.00"), "fee": Decimal("5.00"), "agent": "RUTH", "city": "GIRNE", "compte": "JENOVIC"},
    # 33: VODACOM, ENVOI 63 $, CHRIST, COMM 8 $, GIRNE, JENOVIC
    {"id": 33, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("63.00"), "fee": Decimal("8.00"), "agent": "CHRIST", "city": "GIRNE", "compte": "JENOVIC"},
    # 34: VODACOM, ENVOI 35 $, RUTH, COMM 5 $, GIRNE, JENOVIC
    {"id": 34, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("35.00"), "fee": Decimal("5.00"), "agent": "RUTH", "city": "GIRNE", "compte": "JENOVIC"},
    # 35: ORANGE, ENVOI 20 $, VICTORIA, COMM 5 $, LEFKOSA, JENOVIC
    {"id": 35, "service": "ORANGE_MONEY", "type": "ENVOI", "amount": Decimal("20.00"), "fee": Decimal("5.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 36: AIRTEL, ENVOI 25 $, VICTORIA, COMM 5 $, LEFKOSA, JENOVIC
    {"id": 36, "service": "AIRTEL_MONEY", "type": "ENVOI", "amount": Decimal("25.00"), "fee": Decimal("5.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 37: VODACOM, RETRAIT 44 $, VICTORIA, COMM 8 $, LEFKOSA, JENOVIC
    {"id": 37, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("44.00"), "fee": Decimal("8.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
]

AGENT_EMAILS = {
    "DAV": "david.matamba@fincorya.local",
    "VICTORIA": "victorine.tshonda@fincorya.local",
    "RUTH": "ruth.ngomo@fincorya.local",
    "CHRIS": "christ.kapia@fincorya.local",
    "CHRIST": "christ.kapia@fincorya.local",
}

STAKEHOLDER_NAMES = {
    "JENOVIC": "Mpoto Jenovic",
    "GLODIE": "Glodie Kongolo",
}

@transaction.atomic
def run():
    admin = User.objects.filter(role=Role.ADMIN).first()
    usd = Currency.objects.get(code="USD")
    schedule = TariffSchedule.objects.get(name="FINCORYA PRD V1")
    global_account = GlobalCashAccount.objects.get(currency=usd)

    agents = {code: User.objects.get(email=email) for code, email in AGENT_EMAILS.items()}
    stakeholders = {code: Stakeholder.objects.get(name=name) for code, name in STAKEHOLDER_NAMES.items()}

    print(f"=== ENREGISTREMENT DES 37 TRANSACTIONS SEMAINE 2 (07/09/2026 - 13/09/2026) ===\n")

    created_ops = []
    # Base timestamp for Week 2: 2026-09-08 09:00:00 UTC
    base_ts = datetime(2026, 9, 8, 9, 0, 0, tzinfo=dt_timezone.utc)

    for i, tx in enumerate(RAW_TRANSACTIONS_W2):
        tx_id = tx["id"]
        agent = agents[tx["agent"]]
        stk = stakeholders[tx["compte"]]
        service = tx["service"]
        amount = tx["amount"]
        fee = tx["fee"]
        city = tx["city"]
        op_type = tx["type"]

        cash_account = CashAccount.objects.select_for_update().get(agent=agent, currency=usd)

        # Fee computation
        auto_fee = lookup_fee(schedule, amount)
        manual_fee = fee if fee != auto_fee else None

        # Withdrawal balance verification and automated replenishment from reserve if needed
        if op_type == "RETRAIT":
            payout_needed = amount - fee
            if cash_account.balance < payout_needed:
                needed = (payout_needed - cash_account.balance + Decimal("100.00")).quantize(Decimal("100.00"))
                print(f"  [Trésorerie] Réapprovisionnement de {needed} USD pour l'agent {agent.get_full_name()} (Caisse #{cash_account.pk})...")
                allocate_cash(
                    account_id=cash_account.pk,
                    amount=needed,
                    allocated_by=admin,
                    note=f"Réapprovisionnement caisse opération W2 #{tx_id} (Retrait {amount} USD)"
                )
                cash_account.refresh_from_db()

        idempotency_key = f"OP-TX-{tx_id}-2026-09-W2"

        if op_type == "ENVOI":
            op = create_sent_transfer(
                agent=agent,
                account_id=cash_account.pk,
                amount=amount,
                tariff_schedule=schedule,
                manual_fee=manual_fee,
                service=service,
                customer_identifier=city,
                customer_name=stk.name,
                note=f"Transaction Semaine 2 #{tx_id} - Réseau: {service} - Ville: {city} - Compte: {stk.name}",
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
                customer_name=stk.name,
                note=f"Transaction Semaine 2 #{tx_id} - Réseau: {service} - Ville: {city} - Compte: {stk.name}",
                idempotency_key=idempotency_key,
                fee_mode=FeeMode.DEDUCTED,
            )

        # Set created_at into Week 2 range
        op_time = datetime(2026, 9, 8 + (i % 5), 9 + (i % 8), (i * 3) % 60, tzinfo=dt_timezone.utc)
        Operation.objects.filter(pk=op.pk).update(created_at=op_time)
        op.refresh_from_db()

        cash_account.refresh_from_db()
        created_ops.append(op)
        print(f"✓ Op #{tx_id:02d} | {op.get_type_display():<17} | {amount:>7} USD | Comm: {fee:>3} USD | Agent: {tx['agent']:<8} | Solde caisse: {cash_account.balance:>8} USD | Date: {op.created_at.strftime('%d/%m/%Y %H:%M')}")

    print(f"\n==========================================")
    print(f"Total des 37 opérations enregistrées avec succès.")
    print(f"Nombre d'opérations créées : {len(created_ops)}")
    print(f"Volume Envois : {sum(op.amount for op in created_ops if op.type == OperationType.SENT_TRANSFER)} USD")
    print(f"Volume Retraits : {sum(op.amount for op in created_ops if op.type == OperationType.WITHDRAWAL)} USD")
    print(f"Commissions totales collectées : {sum(op.fee for op in created_ops)} USD")
    print(f"==========================================")

    for code, agent in [("DAV", agents["DAV"]), ("VICTORIA", agents["VICTORIA"]), ("RUTH", agents["RUTH"]), ("CHRIST", agents["CHRIST"])]:
        ca = CashAccount.objects.get(agent=agent, currency=usd)
        print(f"Agent {agent.get_full_name()} ({code}) -> Solde caisse actuel : {ca.balance} USD")

    global_account.refresh_from_db()
    print(f"Caisse Globale (Réserve) -> Solde disponible : {global_account.balance} USD")
    print(f"Capital Total FINCORYA -> {global_account.capital} USD")

if __name__ == "__main__":
    run()
