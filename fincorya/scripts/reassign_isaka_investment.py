import os
import sys
from decimal import Decimal
import django

# Setup Django environment
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction
from apps.stakeholders.models import Stakeholder, Investment
from apps.accounts.models import User, Role

@transaction.atomic
def run():
    isaka_stk = Stakeholder.objects.get(name="Isaka")
    ruth_stk = Stakeholder.objects.filter(name="Ruth Ngomo").first()

    # Reassign investment from Ruth to Isaka
    inv = Investment.objects.filter(amount=Decimal("3400.00")).first()
    if inv:
        inv.stakeholder = isaka_stk
        inv.save(update_fields=["stakeholder"])
        print(f"✓ Apport de 3 400.00 USD réattribué à : {isaka_stk.name} ({isaka_stk.get_type_display()})")

    # Clean up Ruth's stakeholder profile if she only acts as an Agent
    if ruth_stk and not ruth_stk.investments.exists():
        ruth_stk.delete()
        print(f"✓ Profil partie prenante de Ruth Ngomo nettoyé (compte Agent conservé intact).")

    print("\n==========================================")
    print("État révisé des apports au capital :")
    for item in Investment.objects.all().order_by("-amount"):
        print(f"- {item.stakeholder.name:<20} ({item.stakeholder.get_type_display():<13}) : {item.amount:>8} {item.currency.code}")

    total = sum(i.amount for i in Investment.objects.all())
    print(f"TOTAL DU CAPITAL : {total} USD")
    print("==========================================")

if __name__ == "__main__":
    run()
