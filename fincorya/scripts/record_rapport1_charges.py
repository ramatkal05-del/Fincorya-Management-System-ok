"""Enregistre les 3 charges manquantes de « Rapport 1 du mois d'octobre »
liées au ravitaillement initial de 6 000 $ (restart du 1er octobre).

Le capital total du mois (18 960 $, 6 apporteurs) et les 16 transactions de la
semaine de Rapport 1 ne sont PAS importés ici : le capital est déjà enregistré
(Investment #13-18, identique à Rapport 2) et les 16 transactions couvrent une
période antérieure à la reprise du 1er octobre, déjà court-circuitée par un
ajustement direct de solde — les rejouer maintenant corromprait la séquence du
grand livre (le solde est calculé au moment de l'écriture, pas rétroactivement
à la date qu'on lui donne).

Idempotent via accrual_key (contrainte unique sur Expense).
"""
import os
import sys
from datetime import date
from decimal import Decimal

import django

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.accounts.models import User, Role  # noqa: E402
from apps.expenses.models import Expense  # noqa: E402
from apps.expenses.services import decide_expense  # noqa: E402
from apps.finance.locking import ledger_atomic  # noqa: E402
from apps.pricing.models import Currency  # noqa: E402

ADMIN_EMAIL = "fincoryagroup@gmail.com"
ACCRUAL_PREFIX = "RAPPORT-1-OCTOBRE-2026"

CHARGES = [
    ("FORFAIT", "Achat du forfait du mois", Decimal("10.00"), date(2026, 10, 1)),
    ("MONEYGRAM-SEND", "Frais d'envoi MoneyGram du ravitaillement de 6000 USD", Decimal("126.00"), date(2026, 10, 4)),
    ("MONEYGRAM-CYPRUS", "Commission MoneyGram Chypre du ravitaillement", Decimal("15.00"), date(2026, 10, 4)),
]


@ledger_atomic
def run():
    admin = User.objects.get(email__iexact=ADMIN_EMAIL, role=Role.ADMIN, is_active=True)
    usd = Currency.objects.get(code="USD")

    for suffix, label, amount, incurred_on in CHARGES:
        accrual_key = f"{ACCRUAL_PREFIX}-{suffix}"
        if Expense.objects.filter(accrual_key=accrual_key).exists():
            print(f"  Déjà enregistrée : {label} ({amount} USD) — ignorée.")
            continue
        expense = Expense.objects.create(accrual_key=accrual_key, label=label, category="OPERATIONS",
                                          amount=amount, currency=usd, incurred_on=incurred_on, created_by=admin)
        decide_expense(expense_id=expense.pk, actor=admin, decision="APPROVED",
                        comment="Rapport 1 octobre 2026 : charge du ravitaillement initial, déclarée par Jenovic Mpoto.")
        print(f"  {label}: {amount} USD -> Expense #{expense.pk} (approuvée, {incurred_on})")

    total = sum(a for _, _, a, _ in CHARGES)
    print(f"\nTotal des 3 charges : {total} USD")


if __name__ == "__main__":
    run()
