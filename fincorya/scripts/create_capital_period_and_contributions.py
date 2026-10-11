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
from apps.accounts.models import User, Role
from apps.pricing.models import Currency
from apps.cash.models import GlobalCashAccount, MovementDirection
from apps.cash.services import adjust_global_cash
from apps.finance.models import FinancialPeriod, PeriodType, PeriodStatus
from apps.profits.models import ProfitPeriod, ProfitStatus
from apps.stakeholders.models import Stakeholder, StakeholderType, Investment

@transaction.atomic
def run():
    # 1. Admin user & Currency
    admin_user = User.objects.filter(email="fincoryagroup@gmail.com").first()
    if not admin_user:
        admin_user = User.objects.filter(role=Role.ADMIN).first()
    print(f"Administrateur sélectionné : {admin_user.email}")

    usd_currency, _ = Currency.objects.get_or_create(
        code="USD",
        defaults={"name": "Dollar Américain", "symbol": "$", "is_reference": True}
    )

    # 2. Période financière (1ère semaine de septembre : 01/09/2026 au 06/09/2026)
    start_dt = date(2026, 9, 1)
    end_dt = date(2026, 9, 6)

    fin_period, fin_created = FinancialPeriod.objects.get_or_create(
        period_type=PeriodType.WEEK,
        start_date=start_dt,
        end_date=end_dt,
        defaults={
            "timezone_name": "Europe/Istanbul",
            "status": PeriodStatus.OPEN,
        }
    )
    print(f"Période Financière : {fin_period.start_date} au {fin_period.end_date} ({fin_period.get_period_type_display()}) - {'Créée' if fin_created else 'Existante'}")

    profit_period, profit_created = ProfitPeriod.objects.get_or_create(
        start_date=start_dt,
        end_date=end_dt,
        currency=usd_currency,
        defaults={
            "finance_period": fin_period,
            "status": ProfitStatus.DRAFT,
            "calculated_by": admin_user,
        }
    )
    print(f"Période de Bénéfice : {profit_period.start_date} au {profit_period.end_date} [{profit_period.currency.code}] - {'Créée' if profit_created else 'Existante'}")

    # 3. Compte Capital Global (USD)
    global_account, global_created = GlobalCashAccount.objects.get_or_create(
        currency=usd_currency,
        defaults={"administrator": admin_user}
    )
    print(f"Caisse Globale USD : id={global_account.pk}, solde actuel={global_account.balance} $")

    # 4. Apports individuels
    # Table data from user image:
    # MESCHACK BUKASA : 7,000 $
    # GLODIE KONGOLO : 4,320 $
    # RUTH NGOMO : 3,400 $
    # JENOVIC MPOTO : 1,543 $
    # FANTINY PELENDA : 1,500 $
    # JOSEPH OMARI : 1,050 $
    # TOTAL : 18,813 $

    apports = [
        {"name": "Meschack Bukasa", "email": "meschack.bukasa@fincorya.local", "amount": Decimal("7000.00"), "type": StakeholderType.SHAREHOLDER},
        {"name": "Glodie Kongolo", "email": "glodie.kongolo@fincorya.local", "amount": Decimal("4320.00"), "type": StakeholderType.SHAREHOLDER},
        {"name": "Ruth Ngomo", "email": "ruth.ngomo@fincorya.local", "amount": Decimal("3400.00"), "type": StakeholderType.SHAREHOLDER},
        {"name": "Mpoto Jenovic", "email": "jenovich.mpoto@fincorya.local", "amount": Decimal("1543.00"), "type": StakeholderType.SHAREHOLDER},
        {"name": "Fantiny Pelenda", "email": "fantiny@fincorya.local", "amount": Decimal("1500.00"), "type": StakeholderType.PARTNER},
        {"name": "Joseph Omari", "email": "joseph.omari@fincorya.local", "amount": Decimal("1050.00"), "type": StakeholderType.INVESTOR},
    ]

    total_apports = sum(item["amount"] for item in apports)
    print(f"\nTotal des apports calculé : {total_apports} USD (Attendu: 18,813 USD)")

    # Enregistrement des parties prenantes et des investissements
    for item in apports:
        user = User.objects.filter(email=item["email"]).first()
        if user and item["name"] == "Fantiny Pelenda" and user.first_name == "Fantiny":
            user.last_name = "Pelenda"
            user.save(update_fields=["last_name"])

        # Update or create stakeholder
        stk = None
        if user:
            stk = Stakeholder.objects.filter(owner=user).first()
        if not stk:
            stk = Stakeholder.objects.filter(email=item["email"]).first()

        if stk:
            stk.name = item["name"]
            stk.type = item["type"]
            stk.save()
        else:
            stk = Stakeholder.objects.create(
                name=item["name"],
                type=item["type"],
                email=item["email"],
                owner=user,
                is_active=True
            )

        # Create investment record
        inv, inv_created = Investment.objects.get_or_create(
            stakeholder=stk,
            amount=item["amount"],
            currency=usd_currency,
            invested_on=start_dt,
        )
        print(f"Apport enregistré : {stk.name} ({stk.get_type_display()}) -> {item['amount']} USD - {'Nouveau' if inv_created else 'Existant'}")

    # 5. Créditer la caisse globale si elle n'a pas encore le solde
    if global_account.balance < total_apports:
        diff_to_add = total_apports - global_account.balance
        adjust_global_cash(
            global_account_id=global_account.pk,
            direction=MovementDirection.IN,
            amount=diff_to_add,
            adjusted_by=admin_user,
            note="Apports en capital initial - Semaine du 01 au 06 Septembre 2026 (18 813 USD)",
        )
        global_account.refresh_from_db()
        print(f"\nCaisse Globale créditée avec succès. Nouveau solde : {global_account.balance} USD")
    else:
        print(f"\nCaisse Globale dispose déjà d'un solde de : {global_account.balance} USD")

if __name__ == "__main__":
    run()
