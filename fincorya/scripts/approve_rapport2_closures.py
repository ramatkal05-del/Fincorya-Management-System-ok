"""Valide les 5 clôtures en statut REVIEW laissées par l'import de Rapport 2
(écart cash compté vs solde théorique, cumul net -2658 USD).

Justification : les écarts sont réels et documentés par les deux rapports
reçus (OCTOBRE 1.xlsx / Rapport 1, et FINCORYA_Rapport_2_Octobre_2026.xlsx /
Rapport 2) — remises et réapprovisionnements de trésorerie survenus pendant
la semaine mais non détaillés dans les transactions listées. Le cash compté
déclaré par Jenovic Mpoto fait foi ; l'écart est absorbé par un ajustement
tracé (apps.cash.services.approve_closure_correction), jamais par une
modification directe du solde.

Idempotent : une clôture déjà CLOSED est simplement signalée, pas retouchée.
"""
import os
import sys

import django

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.accounts.models import User, Role  # noqa: E402
from apps.cash.models import CashAccount, ClosureStatus, DailyClosure  # noqa: E402
from apps.cash.services import approve_closure_correction  # noqa: E402
from apps.finance.locking import ledger_atomic  # noqa: E402
from apps.pricing.models import Currency  # noqa: E402
from datetime import date  # noqa: E402

ADMIN_EMAIL = "fincoryagroup@gmail.com"
PERIOD_END = date(2026, 10, 11)

ACCOUNTS = {
    "VICTORIA": "thondavictorine@gmail.com",
    "DAV": "nsukudav@gmail.com",
    "CHRIS": "christkapia1@gmail.com",
    "GRACE": "tubadigrace6@gmail.com",
    "GO TRAVEL (Fantiny)": "gotravel.fantiny@fincorya.local",
}

JUSTIFICATION = (
    "Écart validé selon Rapports 1 et 2 (Jenovic Mpoto) : remises/réapprovisionnements "
    "non détaillés dans les transactions listées. Cash compté déclaré fait foi."
)


@ledger_atomic
def run():
    admin = User.objects.get(email__iexact=ADMIN_EMAIL, role=Role.ADMIN, is_active=True)
    usd = Currency.objects.get(code="USD")

    for label, email in ACCOUNTS.items():
        account = CashAccount.objects.select_for_update().get(agent__email__iexact=email, currency=usd)
        closure = DailyClosure.objects.filter(account=account, business_date=PERIOD_END).first()
        if closure is None:
            print(f"  {label:<20} aucune clôture au {PERIOD_END} — rien à faire.")
            continue
        if closure.status == ClosureStatus.CLOSED:
            print(f"  {label:<20} déjà CLOSED (écart {closure.variance}) — ignorée.")
            continue
        if closure.status != ClosureStatus.REVIEW:
            print(f"  {label:<20} statut inattendu {closure.status} — vérification manuelle requise.")
            continue
        before_variance = closure.variance
        approve_closure_correction(closure_id=closure.pk, actor=admin, justification=JUSTIFICATION)
        account.refresh_from_db()
        print(f"  {label:<20} écart {before_variance:>9} corrigé -> solde final {account.balance} USD, "
              f"caisse déverrouillée.")


if __name__ == "__main__":
    run()
