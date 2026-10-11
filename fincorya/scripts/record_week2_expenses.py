import os
import sys
from decimal import Decimal
from datetime import date
import django

# Setup Django environment
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction
from apps.accounts.models import User, Role
from apps.pricing.models import Currency
from apps.expenses.models import Expense, ExpenseStatus, ExpenseCategory, ExpenseApproval, ApprovalLevel, ApprovalDecision
from apps.profits.models import ProfitPeriod
from apps.profits.services import calculate_profit_period, allocate_individual_distributions

@transaction.atomic
def run():
    admin = User.objects.filter(role=Role.ADMIN).first()
    usd = Currency.objects.get(code="USD")

    print("=== ENREGISTREMENT DES 3 CHARGES SEMAINE 2 ===\n")

    expenses_data = [
        {
            "label": "Frais de transfert MoneyGram Kinshasa -> Chypre du Nord (Ravitaillement agents, Net reçu: 4 985 USD)",
            "amount": Decimal("15.00"),
            "category": ExpenseCategory.OPERATIONS,
            "incurred_on": date(2026, 9, 7),
            "note": "Réception le 07/09/2026 - Net reçu : 4 985 $"
        },
        {
            "label": "Coût de retrait et divers",
            "amount": Decimal("60.00"),
            "category": ExpenseCategory.OPERATIONS,
            "incurred_on": date(2026, 9, 9),
            "note": "Frais de retrait et charges diverses"
        },
        {
            "label": "Coût de frais",
            "amount": Decimal("100.00"),
            "category": ExpenseCategory.OPERATIONS,
            "incurred_on": date(2026, 9, 10),
            "note": "Coûts de frais opérationnels"
        },
    ]

    created_expenses = []

    for item in expenses_data:
        exp, created = Expense.objects.get_or_create(
            label=item["label"],
            incurred_on=item["incurred_on"],
            defaults={
                "amount": item["amount"],
                "category": item["category"],
                "currency": usd,
                "status": ExpenseStatus.APPROVED,
                "created_by": admin,
            }
        )
        if not created and exp.status != ExpenseStatus.APPROVED:
            exp.status = ExpenseStatus.APPROVED
            exp.amount = item["amount"]
            exp.save()

        # Enregistrer l'approbation
        ExpenseApproval.objects.get_or_create(
            expense=exp,
            level=ApprovalLevel.ADMIN,
            defaults={
                "decision": ApprovalDecision.APPROVED,
                "decided_by": admin,
                "comment": item["note"]
            }
        )
        created_expenses.append(exp)
        print(f"✓ Charge enregistrée & approuvée : {exp.label} -> {exp.amount} USD (Date: {exp.incurred_on})")

    total_charges = sum(e.amount for e in created_expenses)
    print(f"\nTotal des charges enregistrées pour la Semaine 2 : {total_charges} USD\n")

    # Recalcul de la période de profit Semaine 2
    pp2 = ProfitPeriod.objects.filter(start_date=date(2026, 9, 7)).first()
    if pp2:
        pp2.status = "DRAFT"
        pp2.save(update_fields=["status"])
        calculate_profit_period(period_id=pp2.pk, actor=admin)
        allocate_individual_distributions(period_id=pp2.pk, actor=admin)
        pp2.refresh_from_db()

        print("=== BILAN FINANCIER & RÉPARTITION SEMAINE 2 APRÈS DÉDUCTION DES CHARGES ===")
        print(f"Commissions brutes : {pp2.gross_fees} USD")
        print(f"Charges déductibles : {pp2.deductible_expenses} USD")
        print(f"Bénéfice net à répartir : {pp2.net_profit} USD\n")

        for a in pp2.allocations.all():
            print(f"Enveloppe {a.bucket} ({a.percentage}% = {a.amount} USD) :")
            for d in a.distributions.all():
                print(f"  * {d.stakeholder.name}: {d.amount} USD")

if __name__ == "__main__":
    run()
