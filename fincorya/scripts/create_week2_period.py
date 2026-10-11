import os
import sys
from datetime import date
import django

# Setup Django environment
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.accounts.models import User, Role
from apps.pricing.models import Currency
from apps.finance.models import FinancialPeriod, PeriodType, PeriodStatus
from apps.profits.models import ProfitPeriod, ProfitStatus
from apps.cash.models import CashAccount

def run():
    admin_user = User.objects.filter(email="fincoryagroup@gmail.com").first()
    if not admin_user:
        admin_user = User.objects.filter(role=Role.ADMIN).first()

    usd = Currency.objects.get(code="USD")

    # Semaine 2 : du lundi 07/09/2026 au dimanche 13/09/2026
    start_w2 = date(2026, 9, 7)
    end_w2 = date(2026, 9, 13)

    fp2, fp2_created = FinancialPeriod.objects.get_or_create(
        period_type=PeriodType.WEEK,
        start_date=start_w2,
        end_date=end_w2,
        defaults={
            "timezone_name": "Europe/Istanbul",
            "status": PeriodStatus.OPEN,
        }
    )

    pp2, pp2_created = ProfitPeriod.objects.get_or_create(
        start_date=start_w2,
        end_date=end_w2,
        currency=usd,
        defaults={
            "finance_period": fp2,
            "status": ProfitStatus.DRAFT,
            "calculated_by": admin_user,
        }
    )

    print("=== OUVERTURE SEMAINE 2 ===")
    print(f"Période Financière Semaine 2 : {fp2.start_date} au {fp2.end_date} ({fp2.get_status_display()}) - {'Créée' if fp2_created else 'Existante'}")
    print(f"Période de Bénéfice Semaine 2 : {pp2.start_date} au {pp2.end_date} ({pp2.get_status_display()}) - {'Créée' if pp2_created else 'Existante'}")

    print("\n=== ÉTAT D'OUVERTURE DES CAISSES AGENTS ===")
    for ca in CashAccount.objects.all().order_by("agent__first_name"):
        print(f"Agent: {ca.agent.get_full_name()} | Solde reporté: {ca.balance} {ca.currency.code} | Caisse active: {ca.is_active} | Verrouillée: {ca.is_locked}")

if __name__ == "__main__":
    run()
