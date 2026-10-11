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
from apps.finance.locking import save_internal
from apps.profits.models import ProfitPeriod, ProfitStatus
from apps.profits.services import calculate_profit_period, allocate_individual_distributions

@transaction.atomic
def run():
    admin = User.objects.filter(role=Role.ADMIN).first()
    usd = Currency.objects.get(code="USD")
    business_date = date(2026, 9, 13)

    print(f"=== CLÔTURE DE LA SEMAINE 2 (Date: {business_date}) ===")
    print(f"Administrateur exécutant : {admin.email}\n")

    # 1. Données de comptage physique fournies par l'utilisateur pour les agents (Chypre)
    # VICTORIA : FIN 1,809 $
    # RUTH     : FIN 2,147 $
    # DAV      : FIN 2,148 $
    # CHRIS    : FIN 698 $
    agent_counts = [
        {"email": "victorine.tshonda@fincorya.local", "name": "Victorine Tshonda (VICTORIA)", "declared": Decimal("1809.00"), "justification": "Comptage physique hebdomadaire Semaine 2"},
        {"email": "ruth.ngomo@fincorya.local", "name": "Ruth Ngomo (RUTH)", "declared": Decimal("2147.00"), "justification": "Comptage physique hebdomadaire Semaine 2"},
        {"email": "david.matamba@fincorya.local", "name": "David Matamba (DAV)", "declared": Decimal("2148.00"), "justification": "Comptage physique hebdomadaire Semaine 2"},
        {"email": "christ.kapia@fincorya.local", "name": "Christ Kapia (CHRIS)", "declared": Decimal("698.00"), "justification": "Comptage physique hebdomadaire Semaine 2"},
    ]

    print("--- 1. Clôture des Caisses Physiques d'Agences (Chypre) ---")
    for item in agent_counts:
        agent = User.objects.get(email=item["email"])
        cash_acc = CashAccount.objects.get(agent=agent, currency=usd)

        existing_closure = DailyClosure.objects.filter(account=cash_acc, business_date=business_date).first()
        if not existing_closure or existing_closure.status != ClosureStatus.CLOSED:
            closure = close_cash_day(
                account_id=cash_acc.pk,
                business_date=business_date,
                declared_cash=item["declared"],
                closed_by=admin,
                justification=item["justification"],
            )
            print(f"✓ Caisse {item['name']:<28} | Théorique: {closure.expected_balance:>8} USD | Compté: {item['declared']:>8} USD | Ajustement: {closure.variance:>7} USD | Statut: Clôturée")
        else:
            print(f"✓ Caisse {item['name']:<28} déjà clôturée : {existing_closure.declared_cash} USD")

    # 2. Comptes financiers Kinshasa (Glodie Kongolo)
    # PAYPAL : 350 $
    # AIRTEL : 830 $
    # MPESA  : 1576 $
    # CASH   : 1540 $
    kinshasa_counts = [
        {"code": "PAYPAL-GLODIE-USD", "declared": Decimal("350.00"), "name": "PayPal Kinshasa"},
        {"code": "AIRTEL-GLODIE-USD", "declared": Decimal("830.00"), "name": "Airtel Money Kinshasa"},
        {"code": "MPESA-GLODIE-USD", "declared": Decimal("1576.00"), "name": "M-Pesa Kinshasa"},
        {"code": "CASH-GLODIE-USD", "declared": Decimal("1540.00"), "name": "Cash Kinshasa"},
    ]

    print("\n--- 2. Inventaire et Réconciliation Comptes Kinshasa (Glodie) ---")
    for item in kinshasa_counts:
        fa = FinancialAccount.objects.filter(code=item["code"]).first()
        if fa:
            fa.cached_balance = item["declared"]
            save_internal(fa, update_fields=["cached_balance"])
            print(f"✓ Compte {fa.code:<20} ({item['name']:<22}) : Solde clôturé = {fa.cached_balance:>8} USD")

    total_kinshasa = sum(item["declared"] for item in kinshasa_counts)
    print(f"Total Trésorerie Kinshasa : {total_kinshasa} USD")

    # 3. Clôture de la période financière
    fin_period = FinancialPeriod.objects.filter(start_date=date(2026, 9, 7), end_date=business_date).first()
    if fin_period:
        print(f"\n--- 3. Clôture de la Période Financière Semaine 2 (#{fin_period.pk}) ---")
        for item in agent_counts:
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
                print(f"✓ Inventaire Finance : {fin_acc.code:<22} | Déclaré: {count.declared} USD")

        for item in kinshasa_counts:
            fa = FinancialAccount.objects.filter(code=item["code"]).first()
            if fa:
                count = record_count(
                    period_id=fin_period.pk,
                    account_id=fa.pk,
                    declared=item["declared"],
                    justification="Comptage hebdomadaire Kinshasa Semaine 2",
                    actor=admin,
                )

        fin_period.status = PeriodStatus.LOCKED
        fin_period.locked_at = timezone.now()
        fin_period.locked_by = admin
        save_internal(fin_period)
        print(f"✓ Période Financière Semaine 2 marquée comme CLÔTURÉE / VERROUILLÉE !")

    # 4. Finalisation des Bénéfices Semaine 2
    print("\n--- 4. Bilan & Répartition Définitive des Bénéfices Semaine 2 ---")
    pp2 = ProfitPeriod.objects.filter(start_date=date(2026, 9, 7), end_date=business_date, currency=usd).first()
    if pp2:
        pp2.status = "DRAFT"
        pp2.save(update_fields=["status"])
        calculate_profit_period(period_id=pp2.pk, actor=admin)
        allocate_individual_distributions(period_id=pp2.pk, actor=admin)
        pp2.refresh_from_db()

        print(f"• Commissions brutes collectées (42 opérations) : {pp2.gross_fees} USD")
        print(f"• Charges d'exploitation déduites (3 charges)   : {pp2.deductible_expenses} USD")
        print(f"• BÉNÉFICE NET FINAL DE LA SEMAINE 2            : {pp2.net_profit} USD\n")

        print("Répartition 60 / 20 / 20 :")
        for alloc in pp2.allocations.all():
            print(f"\nEnveloppe {alloc.bucket} ({alloc.percentage}% = {alloc.amount} USD) :")
            for dist in alloc.distributions.all():
                print(f"  * {dist.stakeholder.name:<20} : {dist.amount:>7} USD ({dist.get_status_display()})")

    # 5. État Global de la Trésorerie FINCORYA
    total_agents = sum(item["declared"] for item in agent_counts)
    admin_c = CashAccount.objects.get(agent=admin, currency=usd)
    gc = CashAccount.objects.filter(currency=usd).exclude(agent__in=[admin] + [User.objects.get(email=i["email"]) for i in agent_counts])
    from apps.cash.models import GlobalCashAccount
    global_acc = GlobalCashAccount.objects.first()

    print(f"\n==========================================")
    print(f"SYNTHÈSE DE LA TRÉSORERIE À LA CLÔTURE SEMAINE 2")
    print(f"==========================================")
    print(f"1. Caisses Agences Chypre (DAV, VIC, RUTH, CHRIS) : {total_agents} USD")
    print(f"2. Caisse Administration (Amina Preview)           : {admin_c.balance} USD")
    print(f"3. Comptes Kinshasa (Glodie)                       : {total_kinshasa} USD")
    print(f"4. Réserve Centrale (Disponible)                   : {global_acc.balance} USD")
    total_fincorya = total_agents + admin_c.balance + total_kinshasa + global_acc.balance
    print(f"TOTAL TRÉSORERIE LIQUIDE CONSOLIDÉE FINCORYA      : {total_fincorya} USD")
    print(f"==========================================")

if __name__ == "__main__":
    run()
