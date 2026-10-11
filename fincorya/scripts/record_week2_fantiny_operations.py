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
from apps.accounts.models import User, Role
from apps.pricing.models import Currency, TariffSchedule
from apps.pricing.services import lookup_fee
from apps.cash.models import CashAccount, GlobalCashAccount
from apps.cash.services import allocate_cash
from apps.stakeholders.models import Stakeholder, PartnerOperation
from apps.finance.models import PartnerGuarantee
from apps.operations.models import Operation, OperationType, FeeMode
from apps.operations.services import create_sent_transfer, create_withdrawal
from apps.profits.models import ProfitPeriod
from apps.profits.services import calculate_profit_period, allocate_individual_distributions

RAW_FANTINY_W2 = [
    # 1: VODACOM, RETRAIT 700 $, DAV, COMM 35 $, LEFKOSA
    {"id": 1, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("700.00"), "fee": Decimal("35.00"), "agent": "DAV", "city": "LEFKOSA"},
    # 2: VODACOM, RETRAIT 220 $, DAV, COMM 20 $, LEFKOSA
    {"id": 2, "service": "VODACOM_MPESA", "type": "RETRAIT", "amount": Decimal("220.00"), "fee": Decimal("20.00"), "agent": "DAV", "city": "LEFKOSA"},
    # 3: ORANGE, ENVOI 140 $, RUTH, COMM 15 $, LEFKOSA
    {"id": 3, "service": "ORANGE_MONEY", "type": "ENVOI", "amount": Decimal("140.00"), "fee": Decimal("15.00"), "agent": "RUTH", "city": "LEFKOSA"},
    # 4: ORANGE, RETRAIT 52 $, FANTINY (ADMIN), COMM 8 $, LEFKOSA
    {"id": 4, "service": "ORANGE_MONEY", "type": "RETRAIT", "amount": Decimal("52.00"), "fee": Decimal("8.00"), "agent": "ADMIN", "city": "LEFKOSA"},
    # 5: ORANGE, ENVOI 80 $, RUTH, COMM 8 $, LEFKOSA
    {"id": 5, "service": "ORANGE_MONEY", "type": "ENVOI", "amount": Decimal("80.00"), "fee": Decimal("8.00"), "agent": "RUTH", "city": "LEFKOSA"},
]

AGENT_EMAILS = {
    "DAV": "david.matamba@fincorya.local",
    "RUTH": "ruth.ngomo@fincorya.local",
    "ADMIN": "fincoryagroup@gmail.com",
}

@transaction.atomic
def run():
    admin = User.objects.filter(role=Role.ADMIN).first()
    usd = Currency.objects.get(code="USD")
    schedule = TariffSchedule.objects.get(name="FINCORYA PRD V1")
    global_account = GlobalCashAccount.objects.get(currency=usd)
    fantiny_stk = Stakeholder.objects.get(name="Fantiny Pelenda")

    print(f"=== ENREGISTREMENT DES 5 OPÉRATIONS POUR LE COMPTE DE FANTINY (SEMAINE 2) ===")
    print(f"Partenaire : {fantiny_stk.name} ({fantiny_stk.get_type_display()})\n")

    # 1. Vérification garantie partenaire
    guarantee, _ = PartnerGuarantee.objects.get_or_create(
        stakeholder=fantiny_stk,
        defaults={
            "currency": usd,
            "per_operation_ceiling": Decimal("1000.00"),
            "is_active": True,
            "updated_by": admin,
        }
    )
    if not guarantee.is_active or guarantee.per_operation_ceiling < Decimal("1000.00"):
        guarantee.is_active = True
        guarantee.per_operation_ceiling = Decimal("1000.00")
        guarantee.save()

    agents = {
        "DAV": User.objects.get(email=AGENT_EMAILS["DAV"]),
        "RUTH": User.objects.get(email=AGENT_EMAILS["RUTH"]),
        "ADMIN": admin,
    }

    created_ops = []

    for i, tx in enumerate(RAW_FANTINY_W2):
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

        # Vérification de liquidité pour retraits
        if op_type == "RETRAIT":
            payout_needed = amount - fee
            if cash_account.balance < payout_needed:
                needed = (payout_needed - cash_account.balance + Decimal("100.00")).quantize(Decimal("100.00"))
                print(f"  [Trésorerie] Réapprovisionnement de {needed} USD pour la caisse {agent.get_full_name()}...")
                allocate_cash(
                    account_id=cash_account.pk,
                    amount=needed,
                    allocated_by=admin,
                    note=f"Réapprovisionnement caisse opération Fantiny #{tx_id}"
                )
                cash_account.refresh_from_db()

        idempotency_key = f"OP-FANTINY-W2-{tx_id}"

        if op_type == "ENVOI":
            op = create_sent_transfer(
                agent=agent,
                account_id=cash_account.pk,
                amount=amount,
                tariff_schedule=schedule,
                manual_fee=manual_fee,
                service=service,
                customer_identifier=city,
                customer_name="Fantiny Pelenda",
                note=f"Opération Partenaire #{tx_id} (Fantiny) - Réseau: {service} - Ville: {city}",
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
                customer_name="Fantiny Pelenda",
                note=f"Opération Partenaire #{tx_id} (Fantiny) - Réseau: {service} - Ville: {city}",
                idempotency_key=idempotency_key,
                fee_mode=FeeMode.DEDUCTED,
            )

        # Date dans la Semaine 2 (ex: 11/09/2026 - 12/09/2026)
        op_time = datetime(2026, 9, 10 + (i % 3), 14 + (i % 4), (i * 7) % 60, tzinfo=dt_timezone.utc)
        Operation.objects.filter(pk=op.pk).update(created_at=op_time)
        op.refresh_from_db()

        # Attribution partenaire
        PartnerOperation.objects.get_or_create(
            operation=op,
            defaults={
                "stakeholder": fantiny_stk,
                "share_percent": fantiny_stk.partner_share_percent,
            }
        )

        cash_account.refresh_from_db()
        created_ops.append(op)
        print(f"✓ Op Fantiny #{tx_id} | {op.get_type_display():<17} | {amount:>6} USD | Comm: {fee:>2} USD | Agent: {tx['agent']:<6} ({agent.get_full_name()}) | Solde caisse: {cash_account.balance:>7} USD")

    print(f"\n==========================================")
    print(f"Total des 5 opérations Fantiny enregistrées avec succès.")
    print(f"Commissions générées pour Fantiny : {sum(op.fee for op in created_ops)} USD")
    print(f"==========================================")

    # Recalcul de la période de bénéfice Semaine 2
    pp2 = ProfitPeriod.objects.filter(start_date="2026-09-07").first()
    if pp2:
        # Reset to DRAFT to recalculate cleanly
        pp2.status = "DRAFT"
        pp2.save(update_fields=["status"])
        calculate_profit_period(period_id=pp2.pk, actor=admin)
        allocate_individual_distributions(period_id=pp2.pk, actor=admin)
        pp2.refresh_from_db()
        print(f"\nPériode Semaine 2 recalculée avec les nouvelles opérations :")
        print(f"- Total Commissions brutes Semaine 2 : {pp2.gross_fees} USD")
        print(f"- Bénéfice net Semaine 2 : {pp2.net_profit} USD")
        for a in pp2.allocations.all():
            print(f"  * {a.bucket} ({a.percentage}%): {a.amount} USD")

if __name__ == "__main__":
    run()
