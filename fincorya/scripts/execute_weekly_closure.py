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
from django.utils import timezone
from apps.accounts.models import User, Role
from apps.pricing.models import Currency
from apps.cash.models import CashAccount, DailyClosure, ClosureStatus
from apps.cash.services import close_cash_day
from apps.finance.models import FinancialPeriod, FinancialAccount, PeriodStatus
from apps.finance.closing import record_count
from apps.profits.models import ProfitPeriod, ProfitStatus, Allocation, Distribution
from apps.profits.services import calculate_profit_period, allocate_individual_distributions

@transaction.atomic
def run():
    admin = User.objects.filter(role=Role.ADMIN).first()
    usd = Currency.objects.get(code="USD")
    business_date = date(2026, 9, 6)

    print(f"=== EXÉCUTION DE LA CLÔTURE HEBDOMADAIRE (Date: {business_date}) ===")
    print(f"Administrateur : {admin.email}\n")

    # 1. Données de comptage de fin de semaine fournies par l'utilisateur
    # DAVID : DEBUT 1,700 $ | FIN 1,879 $ | PAYS: CHYPRE
    # VICTORIA : DEBUT 355 $ | FIN 248 $ | PAYS: CHYPRE
    # RUTH : DEBUT 290 $ | FIN 238 $ | PAYS: CHYPRE
    # CHRIS : DEBUT 479 $ | FIN 448 $ | PAYS: CHYPRE
    # GLODIE : DEBUT 3,575 $ | FIN 4,275 $ | PAYS: RDC

    agent_closures = [
        {"email": "david.matamba@fincorya.local", "name": "David Matamba (DAV)", "declared": Decimal("1879.00"), "justification": "Comptage physique hebdomadaire Chypre"},
        {"email": "victorine.tshonda@fincorya.local", "name": "Victorine Tshonda (VICTORIA)", "declared": Decimal("248.00"), "justification": "Comptage physique hebdomadaire Chypre"},
        {"email": "ruth.ngomo@fincorya.local", "name": "Ruth Ngomo (RUTH)", "declared": Decimal("238.00"), "justification": "Comptage physique hebdomadaire Chypre"},
        {"email": "christ.kapia@fincorya.local", "name": "Christ Kapia (CHRIS)", "declared": Decimal("448.00"), "justification": "Comptage physique hebdomadaire Chypre"},
    ]

    print("--- 1. Clôture des caisses opérationnelles agents ---")
    for item in agent_closures:
        agent = User.objects.get(email=item["email"])
        cash_acc = CashAccount.objects.get(agent=agent, currency=usd)
        
        # Clôture journalière / hebdomadaire
        existing_closure = DailyClosure.objects.filter(account=cash_acc, business_date=business_date).first()
        if not existing_closure or existing_closure.status != ClosureStatus.CLOSED:
            closure = close_cash_day(
                account_id=cash_acc.pk,
                business_date=business_date,
                declared_cash=item["declared"],
                closed_by=admin,
                justification=item["justification"],
            )
            print(f"✓ Caisse {item['name']:<28} | Théorique: {closure.expected_balance:>8} USD | Compté: {item['declared']:>8} USD | Écart: {closure.variance:>7} USD | Statut: Clôturée")
        else:
            print(f"✓ Caisse {item['name']:<28} déjà clôturée : {existing_closure.declared_cash} USD")

    # 2. Enregistrement des comptages dans le registre financier
    fin_period = FinancialPeriod.objects.filter(start_date=date(2026, 9, 1), end_date=business_date).first()
    if fin_period:
        print(f"\n--- 2. Enregistrement des inventaires sur la Période Financière #{fin_period.pk} ---")
        for item in agent_closures:
            agent = User.objects.get(email=item["email"])
            fin_acc = FinancialAccount.objects.filter(responsible_user=agent, currency=usd).first()
            if fin_acc:
                count = record_count(
                    period_id=fin_period.pk,
                    account_id=fin_acc.pk,
                    declared=item["declared"],
                    justification=item["justification"],
                    actor=admin,
                )
                print(f"✓ Comptage Finance : {fin_acc.code:<22} | Déclaré: {count.declared} USD")

    # 3. Finalisation des Bénéfices & Répartition 60/20/20
    print("\n--- 3. Calcul & Finalisation des Bénéfices de la semaine ---")
    profit_period = ProfitPeriod.objects.filter(start_date=date(2026, 9, 1), end_date=business_date, currency=usd).first()
    if profit_period:
        if profit_period.status != ProfitStatus.FINALIZED:
            profit_period = calculate_profit_period(period_id=profit_period.pk, actor=admin)
            print(f"✓ Période de bénéfice #{profit_period.pk} FINALISÉE avec succès !")
        else:
            print(f"Période de bénéfice #{profit_period.pk} déjà finalisée.")

        print(f"  • Commissions brutes collectées  : {profit_period.gross_fees} USD")
        print(f"  • Charges déductibles approuvées : {profit_period.deductible_expenses} USD")
        print(f"  • BÉNÉFICE NET DE LA SEMAINE    : {profit_period.net_profit} USD")

        print("\n--- 4. Répartition de l'enveloppe 60/20/20 ---")
        for alloc in profit_period.allocations.all():
            print(f"  • {alloc.bucket:<14} ({alloc.percentage} %) : {alloc.amount} USD")

        # Allocation individuelle aux actionnaires et investisseurs
        print("\n--- 5. Attribution des parts individuelles ---")
        allocate_individual_distributions(period_id=profit_period.pk, actor=admin)
        profit_period.refresh_from_db()

        for alloc in profit_period.allocations.all():
            print(f"\n[Enveloppe {alloc.bucket} - Total : {alloc.amount} USD]")
            for dist in alloc.distributions.all():
                print(f"  -> {dist.stakeholder.name:<20} ({dist.stakeholder.get_type_display():<13}) : {dist.amount:>7} USD (Statut: {dist.get_status_display()})")

    print("\n==========================================")
    print("CLÔTURE HEBDOMADAIRE TERMINÉE AVEC SUCCÈS")
    print("==========================================")

if __name__ == "__main__":
    run()
