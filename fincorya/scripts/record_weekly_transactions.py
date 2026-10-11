import os
import sys
from decimal import Decimal
from datetime import datetime, timezone as dt_timezone
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

# The 28 transactions from the user image:
RAW_TRANSACTIONS = [
    # 1: VODACOM, ENVOI 70 $, VICTORIA, COMM 8 $, LEFKOSA, JENOVIC
    {"id": 1, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("70.00"), "fee": Decimal("8.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 2: ORANGE, ENVOI 28 $, DAV, COMM 5 $, LEFKOSA, JENOVIC
    {"id": 2, "service": "ORANGE_MONEY", "type": "ENVOI", "amount": Decimal("28.00"), "fee": Decimal("5.00"), "agent": "DAV", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 3: VODACOM, ENVOI 20 $, CHRIS, COMM 5 $, GIRNE, GLODIE
    {"id": 3, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("20.00"), "fee": Decimal("5.00"), "agent": "CHRIS", "city": "GIRNE", "compte": "GLODIE"},
    # 4: VODACOM, RETRAIT 92 $, DAV, COMM 8 $, LEFKOSA, GLODIE
    {"id": 4, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("92.00"), "fee": Decimal("8.00"), "agent": "DAV", "city": "LEFKOSA", "compte": "GLODIE"},
    # 5: ORANGE, ENVOI 104 $, VICTORIA, COMM 8 $, LEFKOSA, JENOVIC
    {"id": 5, "service": "ORANGE_MONEY", "type": "ENVOI", "amount": Decimal("104.00"), "fee": Decimal("8.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 6: VODACOM, ENVOI 50 $, CHRIS, COMM 8 $, GIRNE, JENOVIC
    {"id": 6, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("50.00"), "fee": Decimal("8.00"), "agent": "CHRIS", "city": "GIRNE", "compte": "JENOVIC"},
    # 7: VODACOM, RETRAIT 52 $, DAV, COMM 8 $, LEFKOSA, GLODIE
    {"id": 7, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("52.00"), "fee": Decimal("8.00"), "agent": "DAV", "city": "LEFKOSA", "compte": "GLODIE"},
    # 8: VODACOM, ENVOI 20 $, VICTORIA, COMM 5 $, LEFKOSA, GLODIE
    {"id": 8, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("20.00"), "fee": Decimal("5.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "GLODIE"},
    # 9: VODACOM, RETRAIT 440 $, VICTORIA, COMM 30 $, LEFKOSA, JENOVIC
    {"id": 9, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("440.00"), "fee": Decimal("30.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 10: VODACOM, RETRAIT 480 $, VICTORIA, COMM 30 $, LEFKOSA, JENOVIC
    {"id": 10, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("480.00"), "fee": Decimal("30.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 11: VODACOM, RETRAIT 115 $, VICTORIA, COMM 15 $, LEFKOSA, JENOVIC
    {"id": 11, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("115.00"), "fee": Decimal("15.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 12: VODACOM, RETRAIT 30 $, RUTH, COMM 5 $, GIRNE, JENOVIC
    {"id": 12, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("30.00"), "fee": Decimal("5.00"), "agent": "RUTH", "city": "GIRNE", "compte": "JENOVIC"},
    # 13: ORANGE, ENVOI 22 $, CHRIS, COMM 5 $, GIRNE, JENOVIC
    {"id": 13, "service": "ORANGE_MONEY", "type": "ENVOI", "amount": Decimal("22.00"), "fee": Decimal("5.00"), "agent": "CHRIS", "city": "GIRNE", "compte": "JENOVIC"},
    # 14: ORANGE, ENVOI 25 $, CHRIS, COMM 5 $, GIRNE, JENOVIC
    {"id": 14, "service": "ORANGE_MONEY", "type": "ENVOI", "amount": Decimal("25.00"), "fee": Decimal("5.00"), "agent": "CHRIS", "city": "GIRNE", "compte": "JENOVIC"},
    # 15: ORANGE, RETRAIT 240 $, RUTH, COMM 20 $, GIRNE, JENOVIC
    {"id": 15, "service": "ORANGE_MONEY", "type": "RETRAIT", "amount": Decimal("240.00"), "fee": Decimal("20.00"), "agent": "RUTH", "city": "GIRNE", "compte": "JENOVIC"},
    # 16: VODACOM, RETRAIT 40 $, DAV, COMM 5 $, LEFKOSA, GLODIE
    {"id": 16, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("40.00"), "fee": Decimal("5.00"), "agent": "DAV", "city": "LEFKOSA", "compte": "GLODIE"},
    # 17: AIRTEL, ENVOI 42 $, VICTORIA, COMM 5 $, LEFKOSA, JENOVIC
    {"id": 17, "service": "AIRTEL_MONEY", "type": "ENVOI", "amount": Decimal("42.00"), "fee": Decimal("5.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 18: ORANGE, ENVOI 32 $, CHRIS, COMM 5 $, GIRNE, JENOVIC
    {"id": 18, "service": "ORANGE_MONEY", "type": "ENVOI", "amount": Decimal("32.00"), "fee": Decimal("5.00"), "agent": "CHRIS", "city": "GIRNE", "compte": "JENOVIC"},
    # 19: ORANGE, ENVOI 27 $, CHRIS, COMM 5 $, GIRNE, JENOVIC
    {"id": 19, "service": "ORANGE_MONEY", "type": "ENVOI", "amount": Decimal("27.00"), "fee": Decimal("5.00"), "agent": "CHRIS", "city": "GIRNE", "compte": "JENOVIC"},
    # 20: VODACOM, RETRAIT 504 $, RUTH, COMM 26 $, GIRNE, JENOVIC
    {"id": 20, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("504.00"), "fee": Decimal("26.00"), "agent": "RUTH", "city": "GIRNE", "compte": "JENOVIC"},
    # 21: AIRTEL, ENVOI 1,000 $, DAV, COMM 40 $, LEFKOSA, JENOVIC
    {"id": 21, "service": "AIRTEL_MONEY", "type": "ENVOI", "amount": Decimal("1000.00"), "fee": Decimal("40.00"), "agent": "DAV", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 22: AIRTEL, RETRAIT 52 $, RUTH, COMM 8 $, GIRNE, JENOVIC
    {"id": 22, "service": "AIRTEL_MONEY", "type": "RETRAIT", "amount": Decimal("52.00"), "fee": Decimal("8.00"), "agent": "RUTH", "city": "GIRNE", "compte": "JENOVIC"},
    # 23: AIRTEL, ENVOI 10 $, RUTH, COMM 5 $, GIRNE, JENOVIC
    {"id": 23, "service": "AIRTEL_MONEY", "type": "ENVOI", "amount": Decimal("10.00"), "fee": Decimal("5.00"), "agent": "RUTH", "city": "GIRNE", "compte": "JENOVIC"},
    # 24: ORANGE, ENVOI 200 $, RUTH, COMM 15 $, GIRNE, JENOVIC
    {"id": 24, "service": "ORANGE_MONEY", "type": "ENVOI", "amount": Decimal("200.00"), "fee": Decimal("15.00"), "agent": "RUTH", "city": "GIRNE", "compte": "JENOVIC"},
    # 25: ORANGE, ENVOI 50 $, VICTORIA, COMM 8 $, LEFKOSA, JENOVIC
    {"id": 25, "service": "ORANGE_MONEY", "type": "ENVOI", "amount": Decimal("50.00"), "fee": Decimal("8.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 26: AIRTEL, RETRAIT 92 $, VICTORIA, COMM 8 $, LEFKOSA, JENOVIC
    {"id": 26, "service": "AIRTEL_MONEY", "type": "RETRAIT", "amount": Decimal("92.00"), "fee": Decimal("8.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 27: VODACOM, ENVOI 20 $, DAV, COMM 5 $, LEFKOSA, JENOVIC
    {"id": 27, "service": "VODACOM_MPESA", "type": "ENVOI", "amount": Decimal("20.00"), "fee": Decimal("5.00"), "agent": "DAV", "city": "LEFKOSA", "compte": "JENOVIC"},
    # 28: VODACOM, RETRAIT 35 $, DAV, COMM 5 $, LEFKOSA, GLODIE
    {"id": 28, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("35.00"), "fee": Decimal("5.00"), "agent": "DAV", "city": "LEFKOSA", "compte": "GLODIE"},
]

AGENT_EMAILS = {
    "DAV": "david.matamba@fincorya.local",
    "VICTORIA": "victorine.tshonda@fincorya.local",
    "RUTH": "ruth.ngomo@fincorya.local",
    "CHRIS": "christ.kapia@fincorya.local",
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

    print(f"Initialisation de l'enregistrement des 28 transactions...\n")

    created_ops = []

    for tx in RAW_TRANSACTIONS:
        tx_id = tx["id"]
        agent = agents[tx["agent"]]
        stk = stakeholders[tx["compte"]]
        service = tx["service"]
        amount = tx["amount"]
        fee = tx["fee"]
        city = tx["city"]
        op_type = tx["type"]

        cash_account = CashAccount.objects.select_for_update().get(agent=agent, currency=usd)

        # Calcul auto des frais pour comparer avec la commission saisie
        auto_fee = lookup_fee(schedule, amount)
        manual_fee = fee if fee != auto_fee else None

        # Vérification du solde pour les retraits et réapprovisionnement automatique depuis la caisse globale si nécessaire
        if op_type == "RETRAIT":
            # Le montant décaissé pour un retrait avec frais déduits est (amount - fee)
            payout_needed = amount - fee
            if cash_account.balance < payout_needed:
                needed = (payout_needed - cash_account.balance + Decimal("100.00")).quantize(Decimal("100.00"))
                print(f"  [Trésorerie] Réapprovisionnement de {needed} USD pour l'agent {agent.get_full_name()} (Caisse #{cash_account.pk})...")
                allocate_cash(
                    account_id=cash_account.pk,
                    amount=needed,
                    allocated_by=admin,
                    note=f"Réapprovisionnement caisse opération #{tx_id} (Retrait {amount} USD)"
                )
                cash_account.refresh_from_db()

        idempotency_key = f"OP-TX-{tx_id}-2026-09-W1"

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
                note=f"Transaction #{tx_id} - Réseau: {service} - Ville: {city} - Compte: {stk.name}",
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
                note=f"Transaction #{tx_id} - Réseau: {service} - Ville: {city} - Compte: {stk.name}",
                idempotency_key=idempotency_key,
                fee_mode=FeeMode.DEDUCTED,
            )

        cash_account.refresh_from_db()
        created_ops.append(op)
        print(f"✓ Op #{tx_id:02d} | {op.get_type_display():<17} | {amount:>7} USD | Comm: {fee:>3} USD | Agent: {tx['agent']:<8} | Solde caisse: {cash_account.balance:>8} USD | Ref: {op.reference}")

    print(f"\n==========================================")
    print(f"Total des 28 opérations enregistrées avec succès.")
    print(f"Nombre d'opérations créées : {len(created_ops)}")
    print(f"Volume Envois : {sum(op.amount for op in created_ops if op.type == OperationType.SENT_TRANSFER)} USD")
    print(f"Volume Retraits : {sum(op.amount for op in created_ops if op.type == OperationType.WITHDRAWAL)} USD")
    print(f"Commissions totales collectées : {sum(op.fee for op in created_ops)} USD")
    print(f"==========================================")

    for code, agent in agents.items():
        ca = CashAccount.objects.get(agent=agent, currency=usd)
        print(f"Agent {agent.get_full_name()} ({code}) -> Solde final caisse : {ca.balance} USD")

    global_account.refresh_from_db()
    print(f"Caisse Globale (Réserve) -> Solde final : {global_account.balance} USD")
    print(f"Capital Total FINCORYA -> {global_account.capital} USD")

if __name__ == "__main__":
    run()
