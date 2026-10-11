import os
import sys
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
import django

# Setup Django environment
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction
from django.utils import timezone
from apps.accounts.models import User, Role
from apps.pricing.models import Currency
from apps.operations.models import Operation
from apps.expenses.models import Expense
from apps.profits.models import ProfitPeriod, ProfitStatus, Allocation, Distribution
from apps.profits.services import calculate_profit_period, allocate_individual_distributions

@transaction.atomic
def run():
    admin = User.objects.filter(role=Role.ADMIN).first()
    tz = ZoneInfo("Europe/Istanbul")
    usd = Currency.objects.get(code="USD")

    # Mettre à jour les dates des opérations sur la semaine du 01/09/2026 au 06/09/2026
    ops = Operation.objects.all().order_by("pk")
    total_ops = ops.count()
    print(f"Alignement des dates des {total_ops} opérations sur la période du 01/09/2026 au 06/09/2026...")

    for i, op in enumerate(ops):
        day_offset = 1 + (i % 6)  # Réparti du 1er au 6 septembre 2026
        hour = 9 + (i % 10)
        dt = datetime(2026, 9, day_offset, hour, 30, 0, tzinfo=tz)
        Operation.objects.filter(pk=op.pk).update(created_at=dt, paid_at=dt)

    # Réinitialiser la période de profit pour la recalculer avec les opérations
    profit_period = ProfitPeriod.objects.filter(start_date=date(2026, 9, 1), end_date=date(2026, 9, 6), currency=usd).first()
    if profit_period:
        profit_period.status = ProfitStatus.DRAFT
        profit_period.save(update_fields=["status"])
        Allocation.objects.filter(period=profit_period).delete()

        # Recalcul du bénéfice de la période
        profit_period = calculate_profit_period(period_id=profit_period.pk, actor=admin)
        print(f"\n✓ Période de bénéfice #{profit_period.pk} recalculée et FINALISÉE avec succès !")

        print(f"  • Commissions brutes collectées  : {profit_period.gross_fees} USD")
        print(f"  • Charges déductibles approuvées : {profit_period.deductible_expenses} USD")
        print(f"  • BÉNÉFICE NET DE LA SEMAINE    : {profit_period.net_profit} USD")

        print("\n--- Répartition de l'enveloppe 60/20/20 ---")
        for alloc in profit_period.allocations.all():
            print(f"  • {alloc.bucket:<14} ({alloc.percentage} %) : {alloc.amount} USD")

        # Allocation individuelle aux actionnaires et investisseurs
        print("\n--- Attribution des parts individuelles aux Parties Prenantes ---")
        allocate_individual_distributions(period_id=profit_period.pk, actor=admin)
        profit_period.refresh_from_db()

        for alloc in profit_period.allocations.all():
            print(f"\n[Enveloppe {alloc.bucket} - Total : {alloc.amount} USD]")
            for dist in alloc.distributions.all():
                print(f"  -> {dist.stakeholder.name:<20} ({dist.stakeholder.get_type_display():<13}) : {dist.amount:>7} USD (Statut: {dist.get_status_display()})")

    print("\n==========================================")
    print("CLÔTURE & RÉPARTITION DU BÉNÉFICE TERMINÉES")
    print("==========================================")

if __name__ == "__main__":
    run()
