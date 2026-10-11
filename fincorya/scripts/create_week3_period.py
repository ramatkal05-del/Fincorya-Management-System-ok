import os
import sys
from datetime import date
import django

# Setup Django environment
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction
from apps.accounts.models import User, Role
from apps.pricing.models import Currency
from apps.finance.models import FinancialPeriod, PeriodType, PeriodStatus
from apps.profits.models import ProfitPeriod, ProfitStatus
from apps.cash.models import CashAccount, GlobalCashAccount
from apps.finance.models import PartnerGuarantee
from apps.stakeholders.models import Stakeholder

@transaction.atomic
def run():
    admin = User.objects.filter(email="fincoryagroup@gmail.com").first()
    if not admin:
        admin = User.objects.filter(role=Role.ADMIN).first()

    usd = Currency.objects.get(code="USD")

    # Dates Semaine 3 : du lundi 14/09/2026 au dimanche 20/09/2026
    start_w3 = date(2026, 9, 14)
    end_w3 = date(2026, 9, 20)

    print("=== CRÉATION & ACTIVATION DE LA SEMAINE 3 (14/09/2026 - 20/09/2026) ===\n")

    # 1. Période Financière Semaine 3
    fp3, fp3_created = FinancialPeriod.objects.get_or_create(
        period_type=PeriodType.WEEK,
        start_date=start_w3,
        end_date=end_w3,
        defaults={
            "timezone_name": "Europe/Istanbul",
            "status": PeriodStatus.OPEN,
        }
    )
    if not fp3_created and fp3.status != PeriodStatus.OPEN:
        from apps.finance.locking import save_internal
        fp3.status = PeriodStatus.OPEN
        save_internal(fp3, update_fields=["status"])

    print(f"✓ Période Financière Semaine 3 : {fp3.start_date} au {fp3.end_date} (Statut: {fp3.get_status_display()}) - {'Créée' if fp3_created else 'Existante'}")

    # 2. Période de Bénéfices Semaine 3
    pp3, pp3_created = ProfitPeriod.objects.get_or_create(
        start_date=start_w3,
        end_date=end_w3,
        currency=usd,
        defaults={
            "finance_period": fp3,
            "status": ProfitStatus.DRAFT,
            "calculated_by": admin,
        }
    )
    if not pp3_created and pp3.status != ProfitStatus.DRAFT:
        pp3.status = ProfitStatus.DRAFT
        pp3.save(update_fields=["status"])

    print(f"✓ Période de Bénéfice Semaine 3 : {pp3.start_date} au {pp3.end_date} (Statut: {pp3.get_status_display()}) - {'Créée' if pp3_created else 'Existante'}")

    # 3. Activation & Déverrouillage des Caisses Opérationnelles
    print("\n=== ÉTAT DES CAISSES OPÉRATIONNELLES (SEMAINE 3) ===")
    for ca in CashAccount.objects.all().order_by("agent__first_name"):
        ca.is_active = True
        ca.is_locked = False
        ca.save()
        print(f"✓ Caisse Agent: {ca.agent.get_full_name():<25} | Solde d'ouverture: {ca.balance:>8} USD | Active: {ca.is_active} | Déverrouillée: {not ca.is_locked}")

    # 4. Garantie Partenaire Fantiny Pelenda
    fantiny_stk = Stakeholder.objects.filter(name="Fantiny Pelenda").first()
    if fantiny_stk:
        guarantee, _ = PartnerGuarantee.objects.get_or_create(
            stakeholder=fantiny_stk,
            defaults={
                "currency": usd,
                "per_operation_ceiling": 1000.00,
                "is_active": True,
                "updated_by": admin,
            }
        )
        if not guarantee.is_active:
            guarantee.is_active = True
            guarantee.save(update_fields=["is_active"])
        print(f"\n✓ Garantie Partenaire ({fantiny_stk.name}) : Active (Plafond par opération = {guarantee.per_operation_ceiling} USD)")

    gc = GlobalCashAccount.objects.first()
    print(f"✓ Réserve Centrale Disponible : {gc.balance} USD")

    print("\n=======================================================")
    print("TOUT EST ACTIVÉ. LE SYSTÈME EST PRÊT POUR LES OPÉRATIONS DE LA SEMAINE 3 !")
    print("=======================================================")

if __name__ == "__main__":
    run()
