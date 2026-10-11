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
from apps.expenses.models import Expense, ExpenseCategory, ApprovalDecision
from apps.expenses.services import decide_expense

@transaction.atomic
def run():
    admin = User.objects.filter(role=Role.ADMIN).first()
    usd = Currency.objects.get(code="USD")

    print(f"Administrateur : {admin.email}")

    # Création de la charge de 6$
    expense = Expense.objects.create(
        label="Transfert 500$ Vodacom vers Flash — Frais réseau",
        category=ExpenseCategory.OPERATIONS,
        amount=Decimal("6.00"),
        currency=usd,
        incurred_on=date(2026, 9, 1),
        created_by=admin,
    )
    print(f"Charge créée : #{expense.pk} — {expense.label} : {expense.amount} {expense.currency.code}")

    # Approbation par l'administrateur
    approval = decide_expense(
        expense_id=expense.pk,
        actor=admin,
        decision=ApprovalDecision.APPROVED,
        comment="Frais validés pour le transfert de trésorerie Vodacom vers Flash (500 USD)"
    )
    expense.refresh_from_db()
    print(f"Statut après décision : {expense.get_status_display()} par {approval.decided_by.get_full_name() or approval.decided_by.email}")

    print("\n==========================================")
    print("Synthèse des charges de la période :")
    for exp in Expense.objects.all():
        print(f"- Ref: {exp.reference} | {exp.label} | {exp.amount} {exp.currency.code} | Date: {exp.incurred_on} | Statut: {exp.get_status_display()}")
    print("==========================================")

if __name__ == "__main__":
    run()
