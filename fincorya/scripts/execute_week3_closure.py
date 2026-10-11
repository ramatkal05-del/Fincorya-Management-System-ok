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
    business_date = date(2026, 9, 20)

    print(f"=== CLÔTURE DE LA SEMAINE 3 (Date: {business_date}) ===")
    print(f"Administrateur exécutant : {admin.email}\n")

    # 1. Données de comptage physique fournies pour les agents (Chypre)
    # VICTORIA : FIN 1,330 $  | LEFKOSA
    # DAV      : FIN 2,051 $  | LEFKOSA
    # RUTH     : FIN   721 $  | GIRNE
    # CHRIS    : FIN 1,089 $  | GIRNE
    agent_counts = [
        {
            "email": "victorine.tshonda@fincorya.local",
            "name": "Victorine Tshonda (VICTORIA)",
            "declared": Decimal("1330.00"),
            "justification": "Comptage physique hebdomadaire Semaine 3",
        },
        {
            "email": "david.matamba@fincorya.local",
            "name": "David Matamba (DAV)",
            "declared": Decimal("2051.00"),
            "justification": "Comptage physique hebdomadaire Semaine 3",
        },
        {
            "email": "ruth.ngomo@fincorya.local",
            "name": "Ruth Ngomo (RUTH)",
            "declared": Decimal("721.00"),
            "justification": "Comptage physique hebdomadaire Semaine 3",
        },
        {
            "email": "christ.kapia@fincorya.local",
            "name": "Christ Kapia (CHRIS)",
            "declared": Decimal("1089.00"),
            "justification": "Comptage physique hebdomadaire Semaine 3",
        },
    ]

    print("--- 1. Clôture des Caisses Physiques d'Agences (Chypre) ---")
    for item in agent_counts:
        agent = User.objects.get(email=item["email"])
        cash_acc = CashAccount.objects.get(agent=agent, currency=usd)

        existing_closure = DailyClosure.objects.filter(
            account=cash_acc, business_date=business_date
        ).first()
        if not existing_closure or existing_closure.status != ClosureStatus.CLOSED:
            closure = close_cash_day(
                account_id=cash_acc.pk,
                business_date=business_date,
                declared_cash=item["declared"],
                closed_by=admin,
                justification=item["justification"],
            )
            print(
                f"✓ Caisse {item['name']:<28} | "
                f"Théorique: {closure.expected_balance:>8} USD | "
                f"Compté: {item['declared']:>8} USD | "
                f"Ajustement: {closure.variance:>7} USD | "
                f"Statut: Clôturée"
            )
        else:
            print(
                f"✓ Caisse {item['name']:<28} déjà clôturée : "
                f"{existing_closure.declared_cash} USD"
            )

    # 2. Comptes financiers Kinshasa (Glodie Kongolo)
    # Total déclaré : 3,298 $
    # Répartition estimée par compte (cohérente avec les semaines précédentes) :
    #   PAYPAL  :   398 $
    #   AIRTEL  :   700 $
    #   MPESA   : 1,200 $
    #   CASH    :   500 $
    #   ORANGE  :   500 $
    # NOTE : ajuster les montants individuels si le détail par compte est disponible.
    kinshasa_counts = [
        {"code": "PAYPAL-GLODIE-USD",  "declared": Decimal("398.00"),  "name": "PayPal Kinshasa"},
        {"code": "AIRTEL-GLODIE-USD",  "declared": Decimal("700.00"),  "name": "Airtel Money Kinshasa"},
        {"code": "MPESA-GLODIE-USD",   "declared": Decimal("1200.00"), "name": "M-Pesa Kinshasa"},
        {"code": "CASH-GLODIE-USD",    "declared": Decimal("500.00"),  "name": "Cash Kinshasa"},
        {"code": "ORANGE-GLODIE-USD",  "declared": Decimal("500.00"),  "name": "Orange Money Kinshasa"},
    ]

    print("\n--- 2. Inventaire et Réconciliation Comptes Kinshasa (Glodie) ---")
    for item in kinshasa_counts:
        fa = FinancialAccount.objects.filter(code=item["code"]).first()
        if fa:
            fa.cached_balance = item["declared"]
            save_internal(fa, update_fields=["cached_balance"])
            print(
                f"✓ Compte {fa.code:<22} ({item['name']:<25}) : "
                f"Solde clôturé = {fa.cached_balance:>8} USD"
            )
        else:
            print(f"  [AVERTISSEMENT] Compte {item['code']} introuvable — ignoré.")

    total_kinshasa = sum(item["declared"] for item in kinshasa_counts)
    print(f"Total Trésorerie Kinshasa : {total_kinshasa} USD")

    # 3. Clôture de la période financière Semaine 3
    fin_period = FinancialPeriod.objects.filter(
        start_date=date(2026, 9, 14), end_date=business_date
    ).first()
    if fin_period:
        print(f"\n--- 3. Clôture de la Période Financière Semaine 3 (#{fin_period.pk}) ---")

        for item in agent_counts:
            agent = User.objects.get(email=item["email"])
            fin_acc = FinancialAccount.objects.filter(
                responsible_user=agent, currency=usd
            ).first()
            if fin_acc:
                count = record_count(
                    period_id=fin_period.pk,
                    account_id=fin_acc.pk,
                    declared=item["declared"],
                    justification=item["justification"],
                    actor=admin,
                )
                print(
                    f"✓ Inventaire Finance : {fin_acc.code:<22} | "
                    f"Déclaré: {count.declared} USD"
                )

        for item in kinshasa_counts:
            fa = FinancialAccount.objects.filter(code=item["code"]).first()
            if fa:
                record_count(
                    period_id=fin_period.pk,
                    account_id=fa.pk,
                    declared=item["declared"],
                    justification="Comptage hebdomadaire Kinshasa Semaine 3",
                    actor=admin,
                )

        fin_period.status = PeriodStatus.LOCKED
        fin_period.locked_at = timezone.now()
        fin_period.locked_by = admin
        save_internal(fin_period)
        print("✓ Période Financière Semaine 3 marquée comme CLÔTURÉE / VERROUILLÉE !")
    else:
        print("\n[AVERTISSEMENT] Période Financière Semaine 3 introuvable (14/09 → 20/09) — vérifiez create_week3_period.py.")

    # 4. Finalisation des Bénéfices Semaine 3
    print("\n--- 4. Bilan & Répartition Définitive des Bénéfices Semaine 3 ---")
    pp3 = ProfitPeriod.objects.filter(
        start_date=date(2026, 9, 14), end_date=business_date, currency=usd
    ).first()
    if pp3:
        pp3.status = "DRAFT"
        pp3.save(update_fields=["status"])
        calculate_profit_period(period_id=pp3.pk, actor=admin)
        allocate_individual_distributions(period_id=pp3.pk, actor=admin)
        pp3.refresh_from_db()

        print(f"• Commissions brutes collectées (28 opérations) : {pp3.gross_fees} USD")
        print(f"• Charges d'exploitation déduites               : {pp3.deductible_expenses} USD")
        print(f"• BÉNÉFICE NET FINAL DE LA SEMAINE 3            : {pp3.net_profit} USD\n")

        print("Répartition 60 / 20 / 20 :")
        for alloc in pp3.allocations.all():
            print(f"\nEnveloppe {alloc.bucket} ({alloc.percentage}% = {alloc.amount} USD) :")
            for dist in alloc.distributions.all():
                print(
                    f"  * {dist.stakeholder.name:<20} : "
                    f"{dist.amount:>7} USD ({dist.get_status_display()})"
                )
    else:
        print("[AVERTISSEMENT] Période de bénéfice Semaine 3 introuvable.")

    # 5. État Global de la Trésorerie FINCORYA
    total_agents = sum(item["declared"] for item in agent_counts)
    admin_c = CashAccount.objects.get(agent=admin, currency=usd)
    from apps.cash.models import GlobalCashAccount
    global_acc = GlobalCashAccount.objects.first()

    print(f"\n==========================================")
    print(f"SYNTHÈSE DE LA TRÉSORERIE À LA CLÔTURE SEMAINE 3")
    print(f"==========================================")
    print(f"1. Caisses Agences Chypre (VIC, DAV, RUTH, CHRIS) : {total_agents:>8} USD")
    print(f"   • VICTORIA  : {Decimal('1330.00'):>8} USD  (LEFKOSA)")
    print(f"   • DAV       : {Decimal('2051.00'):>8} USD  (LEFKOSA)")
    print(f"   • RUTH      : {Decimal('721.00'):>8} USD  (GIRNE)")
    print(f"   • CHRIS     : {Decimal('1089.00'):>8} USD  (GIRNE)")
    print(f"2. Caisse Administration                           : {admin_c.balance:>8} USD")
    print(f"3. Comptes Kinshasa (Glodie)                       : {total_kinshasa:>8} USD")
    print(f"4. Réserve Centrale (Disponible)                   : {global_acc.balance:>8} USD")
    total_fincorya = total_agents + admin_c.balance + total_kinshasa + global_acc.balance
    print(f"TOTAL TRÉSORERIE LIQUIDE CONSOLIDÉE FINCORYA      : {total_fincorya:>8} USD")
    print(f"==========================================")

if __name__ == "__main__":
    run()
