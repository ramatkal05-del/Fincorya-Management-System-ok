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
from apps.pricing.models import Currency
from apps.cash.models import CashAccount, GlobalCashAccount
from apps.cash.services import allocate_cash

@transaction.atomic
def run():
    admin_user = User.objects.filter(email="fincoryagroup@gmail.com").first()
    if not admin_user:
        admin_user = User.objects.filter(role=Role.ADMIN).first()

    usd_currency = Currency.objects.get(code="USD")
    global_account = GlobalCashAccount.objects.get(currency=usd_currency)

    print(f"Administrateur : {admin_user.email}")
    print(f"Caisse Globale USD : Solde avant allocations = {global_account.balance} USD\n")

    # Data from images:
    # DAV (David Matamba) : 1,700 $
    # VICTORIA (Victorine Tshonda) : 355 $
    # RUTH (Ruth Ngomo) : 290 $
    # CHRIS (Christ Kapia) : 479 $

    allocations_data = [
        {"email": "david.matamba@fincorya.local", "name": "David Matamba (DAV)", "amount": Decimal("1700.00")},
        {"email": "victorine.tshonda@fincorya.local", "name": "Victorine Tshonda (VICTORIA)", "amount": Decimal("355.00")},
        {"email": "ruth.ngomo@fincorya.local", "name": "Ruth Ngomo (RUTH)", "amount": Decimal("290.00")},
        {"email": "christ.kapia@fincorya.local", "name": "Christ Kapia (CHRIS)", "amount": Decimal("479.00")},
    ]

    total_allocated = sum(item["amount"] for item in allocations_data)
    print(f"Total à allouer : {total_allocated} USD\n")

    for item in allocations_data:
        agent_user = User.objects.get(email=item["email"])
        cash_account, created = CashAccount.objects.get_or_create(
            agent=agent_user,
            currency=usd_currency,
            defaults={
                "global_account": global_account,
                "is_active": True,
                "allocated_by": admin_user,
            }
        )
        if cash_account.global_account_id != global_account.pk:
            cash_account.global_account = global_account
            cash_account.save(update_fields=["global_account"])

        # Allocate cash from global account to agent cash account
        funding = allocate_cash(
            account_id=cash_account.pk,
            amount=item["amount"],
            allocated_by=admin_user,
            note=f"Allocation initiale pour {item['name']}",
        )
        cash_account.refresh_from_db()
        print(f"✓ Caisse créée et allouée pour {item['name']} : {cash_account.balance} USD (Caisse ID #{cash_account.pk})")

    global_account.refresh_from_db()
    print(f"\n==========================================")
    print(f"Solde restant Caisse Globale (Réserve) : {global_account.balance} USD")
    print(f"Capital total FINCORYA (Réserve + Caisses agents) : {global_account.capital} USD")
    print(f"==========================================")

if __name__ == "__main__":
    run()
