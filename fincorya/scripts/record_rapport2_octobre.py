"""Enregistre le « Rapport 2 / Octobre 2026 » (FINCORYA_Rapport_2_Octobre_2026.xlsx) dans le système.

Contenu importé, dans une seule transaction (tout ou rien) :
  1. Les 6 fonds déclarés sont conservés dans l'ImportRow comme situation de fonds,
     identique au rapport 1. Ils ne constituent pas six nouveaux investissements.
  2. 2 charges « Abonnement mensuel du système » (9 $ + 24 $) -> Expense, approuvées.
  3. 30 transactions agents (Victoria/Dav/Christ/Grâce) -> Operation, via
     create_sent_transfer / create_withdrawal, avec réapprovisionnement automatique
     de la caisse si un retrait dépasserait le solde courant.
  4. 3 transactions GO TRAVEL -> Operation sur une caisse créée pour un nouveau
     compte AGENT dédié (gotravel.fantiny@fincorya.local). Le moteur Finance actif
     exige qu'une caisse (AGENT_CASH) appartienne à un utilisateur role=AGENT ;
     la fiche PARTNER existante de Fantiny (apports/capital, id 10) n'est donc
     pas réutilisable comme titulaire de caisse et reste inchangée.
  5. Clôture de la semaine (close_cash_day) pour les 5 caisses (4 agents + Fantiny)
     avec le cash compté déclaré en fin de rapport. Toute caisse où le compté diffère
     du solde théorique passe en REVIEW (verrouillée) : c'est le comportement normal
     du système, pas une erreur de ce script. Une correction explicite
     (apps.cash.services.approve_closure_correction) sera nécessaire ensuite si besoin.

Hypothèses explicites (documentées, pas des faits vérifiés) :
  - Toutes les commissions sont saisies manuellement (tariff_schedule=None) pour
    respecter exactement les montants du rapport, y compris la ligne #13
    (Chris, retrait 100 $, commission 10 $) qui dépasse le plafond automatique (8 $).
  - Les 30+3 opérations n'ont aucune date/heure précise dans le fichier source : elles
    sont réparties dans l'ordre sur la période du 1 au 11 octobre 2026 (décision
    opérateur), pas la date réelle de chaque transaction.
  - Les dates de versement des fonds déclarés ne sont pas connues : aucun nouvel
    investissement n'est créé à partir de cette situation cumulée.
  - Les soldes négatifs de début de semaine mentionnés par le rapport pour Dav (-41 $)
    et Fantiny (-314 $) ne sont PAS enregistrés dans le système (un solde de caisse
    négatif y est interdit) : ils sont seulement documentés en note dans l'ImportRow,
    comme avance/dette potentielle à clarifier séparément.
  - L'opération #7 de Grâce (retrait 1 000 $, commission 0 $, « transfert sans frais
    en échange de cash » selon la note source) est explicitement exclue : ce n'est pas
    une transaction client confirmée.

Idempotent au niveau du rapport : si l'ImportRow 'RAPPORT-2-OCTOBRE-2026' existe déjà,
le script s'arrête sans rien reproduire. Les opérations individuelles utilisent aussi
des clés d'idempotence (idempotency_key) pour éviter un doublon en cas de nouvelle
tentative après un échec partiel (bien que tout soit dans une seule transaction).
"""
import os
import sys
from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal

import django

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.accounts.models import User, Role  # noqa: E402
from apps.cash.models import CashAccount  # noqa: E402
from apps.cash.services import allocate_cash, close_cash_day  # noqa: E402
from apps.expenses.models import Expense  # noqa: E402
from apps.expenses.services import decide_expense  # noqa: E402
from apps.finance.funds import open_agent_cash  # noqa: E402
from apps.finance.locking import ledger_atomic  # noqa: E402
from apps.finance.models import FinancialPeriod, ImportRow  # noqa: E402
from apps.operations.models import Operation, OperationType  # noqa: E402
from apps.operations.services import create_sent_transfer, create_withdrawal  # noqa: E402
from apps.pricing.models import Currency  # noqa: E402
from apps.stakeholders.models import Stakeholder  # noqa: E402

SOURCE_KEY = "RAPPORT-2-OCTOBRE-2026"
PERIOD_START, PERIOD_END = date(2026, 10, 1), date(2026, 10, 11)

AGENT_EMAILS = {
    "VICTORIA": "thondavictorine@gmail.com",
    "DAV": "nsukudav@gmail.com",
    "CHRIS": "christkapia1@gmail.com",
    "GRACE": "tubadigrace6@gmail.com",
}
FANTINY_PARTNER_EMAIL = "fantiny2016@gmail.com"  # fiche PARTNER existante (apports/capital), inchangée.
GO_TRAVEL_AGENT_EMAIL = "gotravel.fantiny@fincorya.local"  # nouveau compte AGENT dédié, requis par le
GO_TRAVEL_AGENT_NAME = ("GO TRAVEL", "Fantiny")            # moteur Finance (une caisse exige role=AGENT).
ADMIN_EMAIL = "fincoryagroup@gmail.com"

STAKEHOLDER_NAMES = {
    "MESCHACK": "Meschack BUKASA KABANGA",
    "GLODIE": "Glodie kongolo",
    "RUTH": "Ruth Ngomo",
    "JENOVIC": "JENOVIC mpoto MBOkolo",
    "JOSEPH": "Joseph Omari",
    "FANTINY": "Fantiny",
}

# Section 01 — fonds déclarés : (nom, montant, clé stakeholder).
FUNDS_DECLARED = [
    ("MESCHACK", Decimal("7000.00"), "MESCHACK"),
    ("GLODIE", Decimal("4370.00"), "GLODIE"),
    ("RUTH", Decimal("3400.00"), "RUTH"),
    ("JENOVIC", Decimal("1600.00"), "JENOVIC"),
    ("FANTINY", Decimal("1500.00"), "FANTINY"),
    ("JOSEPH", Decimal("1090.00"), "JOSEPH"),
]

# Section 02 — charges de la semaine.
CHARGES = [
    ("Abonnement mensuel du système — ligne 1", Decimal("9.00")),
    ("Abonnement mensuel du système — ligne 2", Decimal("24.00")),
]

NETWORK_TO_SERVICE = {"VODACOM": "VODACOM_MPESA", "AIRTEL": "AIRTEL_MONEY", "ORANGE": "ORANGE_MONEY"}

# Section 04 — 30 transactions de la semaine. Opération #7 (Grâce, retrait 1000$,
# commission 0$) volontairement exclue : voir docstring.
RAW_TRANSACTIONS = [
    {"id": 1, "network": "VODACOM", "type": "ENVOI", "amount": Decimal("200.00"), "fee": Decimal("15.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    {"id": 2, "network": "VODACOM", "type": "RETRAIT", "amount": Decimal("1112.00"), "fee": Decimal("45.00"), "agent": "DAV", "city": "LEFKOSA", "compte": "GLODIE"},
    {"id": 3, "network": "VODACOM", "type": "ENVOI", "amount": Decimal("60.00"), "fee": Decimal("8.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    {"id": 4, "network": "AIRTEL", "type": "ENVOI", "amount": Decimal("50.00"), "fee": Decimal("5.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    {"id": 5, "network": "VODACOM", "type": "ENVOI", "amount": Decimal("50.00"), "fee": Decimal("8.00"), "agent": "DAV", "city": "LEFKOSA", "compte": "JENOVIC"},
    {"id": 6, "network": "ORANGE", "type": "ENVOI", "amount": Decimal("300.00"), "fee": Decimal("17.00"), "agent": "GRACE", "city": "GIRNE", "compte": "JENOVIC"},
    # id 7 exclu.
    {"id": 8, "network": "VODACOM", "type": "RETRAIT", "amount": Decimal("25.00"), "fee": Decimal("5.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    {"id": 9, "network": "VODACOM", "type": "RETRAIT", "amount": Decimal("105.00"), "fee": Decimal("15.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    {"id": 10, "network": "AIRTEL", "type": "ENVOI", "amount": Decimal("15.00"), "fee": Decimal("5.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    {"id": 11, "network": "AIRTEL", "type": "ENVOI", "amount": Decimal("20.00"), "fee": Decimal("5.00"), "agent": "GRACE", "city": "GIRNE", "compte": "JENOVIC"},
    {"id": 12, "network": "AIRTEL", "type": "ENVOI", "amount": Decimal("30.00"), "fee": Decimal("5.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    {"id": 13, "network": "VODACOM", "type": "RETRAIT", "amount": Decimal("100.00"), "fee": Decimal("10.00"), "agent": "CHRIS", "city": "GIRNE", "compte": "JENOVIC"},
    {"id": 14, "network": "VODACOM", "type": "ENVOI", "amount": Decimal("50.00"), "fee": Decimal("8.00"), "agent": "GRACE", "city": "GIRNE", "compte": "JENOVIC"},
    {"id": 15, "network": "VODACOM", "type": "ENVOI", "amount": Decimal("42.00"), "fee": Decimal("8.00"), "agent": "CHRIS", "city": "GIRNE", "compte": "JENOVIC"},
    {"id": 16, "network": "VODACOM", "type": "ENVOI", "amount": Decimal("52.00"), "fee": Decimal("8.00"), "agent": "DAV", "city": "LEFKOSA", "compte": "JENOVIC"},
    {"id": 17, "network": "ORANGE", "type": "ENVOI", "amount": Decimal("15.00"), "fee": Decimal("5.00"), "agent": "CHRIS", "city": "GIRNE", "compte": "JENOVIC"},
    {"id": 18, "network": "ORANGE", "type": "ENVOI", "amount": Decimal("40.00"), "fee": Decimal("5.00"), "agent": "GRACE", "city": "GIRNE", "compte": "JENOVIC"},
    {"id": 19, "network": "VODACOM", "type": "ENVOI", "amount": Decimal("52.00"), "fee": Decimal("8.00"), "agent": "CHRIS", "city": "GIRNE", "compte": "JENOVIC"},
    {"id": 20, "network": "ORANGE", "type": "ENVOI", "amount": Decimal("82.00"), "fee": Decimal("0.00"), "agent": "CHRIS", "city": "GIRNE", "compte": "JENOVIC"},
    {"id": 21, "network": "AIRTEL", "type": "ENVOI", "amount": Decimal("10.00"), "fee": Decimal("5.00"), "agent": "VICTORIA", "city": "GIRNE", "compte": "JENOVIC"},
    {"id": 22, "network": "AIRTEL", "type": "ENVOI", "amount": Decimal("40.00"), "fee": Decimal("5.00"), "agent": "VICTORIA", "city": "GIRNE", "compte": "JENOVIC"},
    {"id": 23, "network": "VODACOM", "type": "RETRAIT", "amount": Decimal("550.00"), "fee": Decimal("30.00"), "agent": "DAV", "city": "GIRNE", "compte": "JENOVIC"},
    {"id": 24, "network": "VODACOM", "type": "RETRAIT", "amount": Decimal("98.00"), "fee": Decimal("8.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    {"id": 25, "network": "VODACOM", "type": "ENVOI", "amount": Decimal("60.00"), "fee": Decimal("8.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    {"id": 26, "network": "ORANGE", "type": "RETRAIT", "amount": Decimal("170.00"), "fee": Decimal("15.00"), "agent": "DAV", "city": "LEFKOSA", "compte": "JENOVIC"},
    {"id": 27, "network": "VODACOM", "type": "RETRAIT", "amount": Decimal("185.00"), "fee": Decimal("15.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "GLODIE"},
    {"id": 28, "network": "AIRTEL", "type": "ENVOI", "amount": Decimal("100.00"), "fee": Decimal("8.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    {"id": 29, "network": "AIRTEL", "type": "ENVOI", "amount": Decimal("10.00"), "fee": Decimal("0.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "JENOVIC"},
    {"id": 30, "network": "VODACOM", "type": "RETRAIT", "amount": Decimal("1396.00"), "fee": Decimal("64.00"), "agent": "VICTORIA", "city": "LEFKOSA", "compte": "GLODIE"},
]

# Section 05 — GO TRAVEL (Fantiny), 3 envois.
GO_TRAVEL_TRANSACTIONS = [
    {"network": "ORANGE", "amount": Decimal("200.00"), "fee": Decimal("13.00"), "city": "LEFKOSA", "compte": "JENOVIC"},
    {"network": "ORANGE", "amount": Decimal("130.00"), "fee": Decimal("15.00"), "city": "LEFKOSA", "compte": "JENOVIC"},
    {"network": "VODACOM", "amount": Decimal("85.00"), "fee": Decimal("8.00"), "city": "MAGUSA", "compte": "JENOVIC"},
]

# Section 06 — cash déclaré en fin de semaine (compté).
DECLARED_CASH_END = {
    "VICTORIA": Decimal("1441.00"),
    "DAV": Decimal("1458.00"),
    "CHRIS": Decimal("522.00"),
    "GRACE": Decimal("142.00"),
    "FANTINY": Decimal("103.00"),
}

# Soldes négatifs rapportés en début de semaine, documentés mais jamais écrits dans
# une caisse (un solde de caisse négatif est interdit par le système).
UNRECORDED_NEGATIVE_OPENING = {"DAV": Decimal("-41.00"), "FANTINY": Decimal("-314.00")}


def _spread_timestamp(index, total):
    day_offset = index * 11 // max(total, 1)
    day_offset = min(day_offset, 10)
    hour = 9 + (index % 8)
    minute = (index * 7) % 60
    return datetime(2026, 10, 1 + day_offset, hour, minute, tzinfo=dt_timezone.utc)


@ledger_atomic
def run():
    if ImportRow.objects.filter(source_key=SOURCE_KEY).exists():
        print("Déjà importé : aucune nouvelle écriture (ImportRow existant).")
        return

    admin = User.objects.get(email__iexact=ADMIN_EMAIL, role=Role.ADMIN, is_active=True)
    months = FinancialPeriod.objects.filter(period_type="MONTH", start_date__lte=date(2026, 10, 31),
                                            end_date__gte=PERIOD_START)
    if months.exclude(start_date=PERIOD_START, end_date=date(2026, 10, 31)).exists():
        raise RuntimeError("Une période mensuelle chevauche octobre ; rapprocher avant import.")
    if FinancialPeriod.objects.filter(status="LOCKED", start_date__lte=date(2026, 10, 31),
                                      end_date__gte=PERIOD_START).exists():
        raise RuntimeError("Une période chevauchante est verrouillée ; import refusé.")
    FinancialPeriod.objects.get_or_create(period_type="MONTH", start_date=PERIOD_START,
                                          end_date=date(2026, 10, 31))
    usd = Currency.objects.get(code="USD")
    agents = {code: User.objects.get(email__iexact=email, role=Role.AGENT, is_active=True)
              for code, email in AGENT_EMAILS.items()}
    stakeholders = {code: Stakeholder.objects.get(name=name) for code, name in STAKEHOLDER_NAMES.items()}

    report = {"source": "FINCORYA_Rapport_2_Octobre_2026.xlsx", "period": [str(PERIOD_START), str(PERIOD_END)],
               "excluded_operations": ["#7 Grâce retrait 1000$ commission 0$ (échange sans frais, non confirmé)"],
               "unrecorded_negative_opening": {k: str(v) for k, v in UNRECORDED_NEGATIVE_OPENING.items()},
               "note": "Avance/dette potentielle à clarifier séparément ; aucun impact sur une caisse."}

    # 1) Situation cumulée déjà présente dans le rapport 1, pas un versement.
    print("=== 1/5 Fonds déclarés ===")
    report["declared_funds"] = []
    for label, amount, key in FUNDS_DECLARED:
        stakeholder = stakeholders[key]
        report["declared_funds"].append({"stakeholder_id": stakeholder.pk, "name": stakeholder.name,
                                         "amount": str(amount), "currency": "USD"})
        print(f"  {label:<10} {amount:>8} USD : situation déclarée, aucun nouveau versement.")
    assert sum(a for _, a, _ in FUNDS_DECLARED) == Decimal("18960.00")

    # 2) Charges de la semaine.
    print("=== 2/5 Charges ===")
    for label, amount in CHARGES:
        expense = Expense.objects.create(label=label, category="OPERATIONS", amount=amount,
                                          currency=usd, incurred_on=PERIOD_START, created_by=admin)
        decide_expense(expense_id=expense.pk, actor=admin, decision="APPROVED",
                        comment="Rapport 2 octobre 2026 : charge déclarée par Jenovic Mpoto.")
        print(f"  {label}: {amount} USD -> Expense #{expense.pk} (approuvée)")
    assert sum(a for _, a in CHARGES) == Decimal("33.00")

    # 3) 30 transactions agents.
    print("=== 3/5 Transactions agents ===")
    created_main = []
    for i, tx in enumerate(RAW_TRANSACTIONS):
        agent = agents[tx["agent"]]
        account = CashAccount.objects.select_for_update().get(agent=agent, currency=usd)
        service = NETWORK_TO_SERVICE[tx["network"]]
        idempotency_key = f"{SOURCE_KEY}-TX-{tx['id']}"
        op_time = _spread_timestamp(i, len(RAW_TRANSACTIONS))

        if tx["type"] == "RETRAIT":
            payout_needed = tx["amount"] - tx["fee"]
            if account.balance < payout_needed:
                needed = (payout_needed - account.balance + Decimal("50.00")).quantize(Decimal("1."))
                print(f"    [Trésorerie] Réapprovisionnement {needed} USD pour {tx['agent']} (opération #{tx['id']})")
                allocate_cash(account_id=account.pk, amount=needed, allocated_by=admin,
                              note=f"Réapprovisionnement Rapport 2 octobre, opération #{tx['id']}")
                account.refresh_from_db()
            op = create_withdrawal(agent=agent, account_id=account.pk, amount=tx["amount"], tariff_schedule=None,
                                    manual_fee=tx["fee"], fee_mode="DEDUCTED", service=service,
                                    customer_identifier=tx["city"], customer_name=tx["compte"],
                                    note=f"Rapport 2 octobre, opération #{tx['id']} — compte {tx['compte']}",
                                    idempotency_key=idempotency_key)
        else:
            op = create_sent_transfer(agent=agent, account_id=account.pk, amount=tx["amount"], tariff_schedule=None,
                                       manual_fee=tx["fee"], fee_mode="ADDED", service=service,
                                       customer_identifier=tx["city"], customer_name=tx["compte"],
                                       note=f"Rapport 2 octobre, opération #{tx['id']} — compte {tx['compte']}",
                                       idempotency_key=idempotency_key)
        Operation.objects.filter(pk=op.pk).update(created_at=op_time)
        created_main.append(op)
        account.refresh_from_db()
        print(f"  #{tx['id']:>2} {tx['type']:<7} {tx['amount']:>7} USD comm {tx['fee']:>5} USD "
              f"{tx['agent']:<9} solde={account.balance:>8}")

    envois = sum(op.amount for op in created_main if op.type == OperationType.SENT_TRANSFER)
    retraits = sum(op.amount for op in created_main if op.type == OperationType.WITHDRAWAL)
    commissions = sum(op.fee for op in created_main)
    print(f"  Total: {len(created_main)} opérations, envois={envois}, retraits={retraits}, commissions={commissions}")
    assert envois == Decimal("1278.00")
    assert retraits == Decimal("3741.00")  # 4741 du rapport moins les 1000 de l'opération #7 exclue.
    assert commissions == Decimal("343.00")

    # 4) GO TRAVEL — nouveau compte AGENT dédié (le moteur Finance exige role=AGENT
    #    pour toute caisse ; le compte PARTNER existant de Fantiny reste inchangé,
    #    c'est une identité distincte pour le volet opérationnel de GO TRAVEL).
    print("=== 4/5 GO TRAVEL ===")
    go_travel_agent, agent_created = User.objects.get_or_create(
        email=GO_TRAVEL_AGENT_EMAIL,
        defaults={"first_name": GO_TRAVEL_AGENT_NAME[0], "last_name": GO_TRAVEL_AGENT_NAME[1],
                  "role": Role.AGENT, "is_active": True})
    if agent_created:
        go_travel_agent.set_unusable_password()
        go_travel_agent.save(update_fields=["password"])
        print(f"  Compte AGENT créé : {go_travel_agent.email} (#{go_travel_agent.pk}) — "
              f"sans mot de passe utilisable ; usage interne/admin uniquement pour l'instant.")
    else:
        print(f"  Compte AGENT existant réutilisé : {go_travel_agent.email} (#{go_travel_agent.pk}).")

    financial_account = open_agent_cash(actor=admin, agent=go_travel_agent, currency=usd)
    go_travel_account = CashAccount.objects.select_for_update().get(agent=go_travel_agent, currency=usd)
    if go_travel_account.balance or go_travel_account.movements.exists():
        raise RuntimeError("La caisse GO TRAVEL a déjà un historique ; import déjà effectué ou conflit à examiner.")
    if not go_travel_account.is_active:
        go_travel_account.is_active = True
        go_travel_account.allocated_by = admin
        go_travel_account.allocated_at = datetime.now(dt_timezone.utc)
        go_travel_account.save(update_fields=["is_active", "allocated_by", "allocated_at"])
    print(f"  Caisse GO TRAVEL #{go_travel_account.pk} liée au grand livre (FinancialAccount #{financial_account.pk}).")

    created_go_travel = []
    for i, tx in enumerate(GO_TRAVEL_TRANSACTIONS):
        service = NETWORK_TO_SERVICE[tx["network"]]
        idempotency_key = f"{SOURCE_KEY}-GOTRAVEL-{i + 1}"
        op_time = _spread_timestamp(i, len(GO_TRAVEL_TRANSACTIONS))
        op = create_sent_transfer(agent=go_travel_agent, account_id=go_travel_account.pk, amount=tx["amount"],
                                   tariff_schedule=None, manual_fee=tx["fee"], fee_mode="ADDED", service=service,
                                   customer_identifier=tx["city"], customer_name=tx["compte"],
                                   note=f"Rapport 2 octobre, GO TRAVEL #{i + 1} — compte {tx['compte']} (Fantiny)",
                                   idempotency_key=idempotency_key)
        Operation.objects.filter(pk=op.pk).update(created_at=op_time)
        created_go_travel.append(op)
        go_travel_account.refresh_from_db()
        print(f"  GO TRAVEL #{i + 1} {tx['amount']:>6} USD comm {tx['fee']:>4} USD -> solde={go_travel_account.balance}")

    go_travel_envois = sum(op.amount for op in created_go_travel)
    go_travel_commissions = sum(op.fee for op in created_go_travel)
    assert go_travel_envois == Decimal("415.00")
    assert go_travel_commissions == Decimal("36.00")

    # 5) Clôture de la semaine — 4 agents + Fantiny, cash compté déclaré.
    print("=== 5/5 Clôture de la semaine (cash compté) ===")
    closures = {}
    for code, agent in agents.items():
        account = CashAccount.objects.select_for_update().get(agent=agent, currency=usd)
        declared = DECLARED_CASH_END[code]
        closure = close_cash_day(account_id=account.pk, business_date=PERIOD_END, declared_cash=declared,
                                  closed_by=admin,
                                  justification=f"Rapport 2 octobre 2026 : cash compté déclaré par Jenovic Mpoto.")
        closures[code] = {"account_id": account.pk, "declared": str(declared),
                           "expected": str(closure.expected_balance), "variance": str(closure.variance),
                           "status": closure.status}
        print(f"  {code:<9} compté={declared:>8} théorique={closure.expected_balance:>8} "
              f"écart={closure.variance:>8} statut={closure.status}")

    go_travel_closure = close_cash_day(account_id=go_travel_account.pk, business_date=PERIOD_END,
                                        declared_cash=DECLARED_CASH_END["FANTINY"], closed_by=admin,
                                        justification="Rapport 2 octobre 2026 : cash GO TRAVEL compté déclaré par Jenovic Mpoto.")
    closures["FANTINY"] = {"account_id": go_travel_account.pk, "declared": str(DECLARED_CASH_END["FANTINY"]),
                            "expected": str(go_travel_closure.expected_balance), "variance": str(go_travel_closure.variance),
                            "status": go_travel_closure.status}
    print(f"  {'FANTINY':<9} compté={DECLARED_CASH_END['FANTINY']:>8} "
          f"théorique={go_travel_closure.expected_balance:>8} écart={go_travel_closure.variance:>8} "
          f"statut={go_travel_closure.status}")

    report["investments"] = []
    report["closures"] = closures
    row = ImportRow.objects.create(source_key=SOURCE_KEY, payload=report, reviewed_by=admin,
                                    resolution="Rapport 2 octobre 2026 importé : situation des fonds, charges, 32 opérations, "
                                               "GO TRAVEL et clôture de semaine. Écarts de clôture, s'il y en a, "
                                               "restent en revue (REVIEW) et nécessitent une validation séparée.")
    print(f"\nImportRow #{row.pk} créé. Toute caisse en statut REVIEW ci-dessus nécessite "
          f"apps.cash.services.approve_closure_correction avant la prochaine journée.")


if __name__ == "__main__":
    run()
