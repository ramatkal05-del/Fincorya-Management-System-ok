"""Single source of report datasets. Dashboard, HTML, CSV, Excel and PDF all read from `build_report`.

Every report carries an explicit period (dates + timezone), the week convention,
the filters applied, the generating user, a provisional/closed status and an
identifier + version so that a closed report can be reproduced.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Sum
from django.utils import timezone

from apps.accounts.models import Role
from apps.accounts.permissions import finance_policy, linked_party
from config.business_time import business_day_bounds
from .models import (AccountNature, AccountType, FinancialAccount, FinancialPeriod, FundContribution, InternalTransfer,
                     JournalBatch, LedgerEntry, PeriodStatus, RequestStatus, StakeholderRequest)
from .services import ledger_balance

ZERO = Decimal("0.00")
TREASURY_TYPES = ["AGENT_CASH", "GLOBAL_CASH", "MOBILE_MONEY", "DIGITAL", "FINANCIAL_SERVICE"]
WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

REPORT_KINDS = {
    "ACTIVITY": "Rapport d’activité",
    "FUNDS": "Rapport des fonds apportés",
    "TREASURY": "Rapport de trésorerie",
    "COMMISSIONS": "Rapport des commissions",
    "EXPENSES": "Rapport des charges",
    "INVESTORS": "Rapport investisseurs",
    "PARTNERS": "Rapport partenaires",
    "SHAREHOLDERS": "Rapport actionnaires",
    "RESULT": "Rapport mensuel de résultat et distribution",
    "CONTROL": "Rapport de contrôle",
}
PARTY_KIND = {"INVESTOR": "INVESTORS", "PARTNER": "PARTNERS", "SHAREHOLDER": "SHAREHOLDERS"}
MANAGER_KINDS = {"ACTIVITY", "TREASURY", "COMMISSIONS", "EXPENSES", "RESULT", "CONTROL"}


def allowed_kinds(user):
    """Server-side list of report kinds a user may build (download rights are checked separately)."""
    policy = finance_policy(user)
    if policy.administer:
        return dict(REPORT_KINDS)
    if policy.weekly_report:
        return {k: v for k, v in REPORT_KINDS.items() if k in MANAGER_KINDS}
    if policy.own_cash_only:
        return {"ACTIVITY": "Rapport quotidien de mes opérations"}
    party = linked_party(user)
    if party:
        kinds = {PARTY_KIND[party.type]: "Ma situation"}
        if policy.global_read_only:
            kinds["ACTIVITY"] = "Activité globale (lecture)"
            kinds["RESULT"] = REPORT_KINDS["RESULT"]
        return kinds
    return {}


def can_download(user):
    return bool(getattr(user, "is_active", False) and user.role in set(
        getattr(settings, "REPORT_DOWNLOAD_ROLES", [Role.ADMIN, Role.FINANCE_MANAGER, Role.AGENT])))


def week_start():
    return int(getattr(settings, "FINANCE_WEEK_START", 0)) % 7


def period_bounds(preset, anchor, custom_end=None):
    """Return (start, end, label) for an explicit preset around `anchor`."""
    if preset == "DAY":
        return anchor, anchor, f"Journée du {anchor:%d/%m/%Y}"
    if preset == "WEEK":
        start = anchor - timedelta(days=(anchor.weekday() - week_start()) % 7)
        end = start + timedelta(days=6)
        return start, end, f"Semaine du {start:%d/%m/%Y} au {end:%d/%m/%Y} ({WEEKDAYS[week_start()]} → {WEEKDAYS[(week_start() + 6) % 7]})"
    if preset == "MONTH":
        start = anchor.replace(day=1)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return start, end, f"Mois de {start:%m/%Y}"
    if preset == "QUARTER":
        quarter = (anchor.month - 1) // 3
        start = date(anchor.year, quarter * 3 + 1, 1)
        end = (date(anchor.year + (quarter == 3), (quarter + 1) % 4 * 3 + 1, 1) - timedelta(days=1))
        return start, end, f"Trimestre {quarter + 1} {anchor.year}"
    if preset == "YEAR":
        return date(anchor.year, 1, 1), date(anchor.year, 12, 31), f"Année {anchor.year}"
    if preset == "CUSTOM":
        if custom_end is None or custom_end < anchor:
            raise ValidationError("Une période personnalisée exige une date de fin postérieure au début.")
        return anchor, custom_end, f"Du {anchor:%d/%m/%Y} au {custom_end:%d/%m/%Y}"
    raise ValidationError("Période inconnue.")


def _status(start, end):
    locked = FinancialPeriod.objects.filter(status=PeriodStatus.LOCKED, start_date__lte=start, end_date__gte=end).exists()
    return "CLÔTURÉ" if locked else "PROVISOIRE"


def _money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"))


def _section(title, columns, rows, *, numeric=(), totals=None):
    """`totals` is (currency_column, [amount_columns]); total rows are laid out on the same columns as the data."""
    total_rows = _by_currency(rows, len(columns), *totals) if totals else []
    return {"title": title, "columns": columns, "rows": rows, "numeric": list(numeric), "totals": total_rows}


def _by_currency(rows, width, code_index, amount_indexes):
    totals = {}
    for row in rows:
        item = totals.setdefault(row[code_index], {index: ZERO for index in amount_indexes})
        for index in amount_indexes:
            item[index] += _money(row[index])
    return [[f"Total {code}", *[values.get(index, "") for index in range(1, width)]] for code, values in sorted(totals.items())]


def _delta(account, start_at, end_at):
    return ledger_balance(account, before=end_at) - ledger_balance(account, before=start_at)


# --------------------------------------------------------------------------- datasets

def _activity(user, start_at, end_at, filters):
    from apps.operations.models import Operation
    operations = Operation.objects.select_related("currency", "agent", "account").filter(created_at__gte=start_at, created_at__lt=end_at)
    if finance_policy(user).own_cash_only:
        operations = operations.filter(agent=user)
    if filters.get("agent"):
        operations = operations.filter(agent=filters["agent"])
    if filters.get("service"):
        operations = operations.filter(service=filters["service"])
    if filters.get("status"):
        operations = operations.filter(status=filters["status"])
    if filters.get("country"):
        operations = operations.filter(account__financial_account__country_code=filters["country"])
    rows = [[timezone.localtime(op.created_at).strftime("%d/%m/%Y %H:%M"), str(op.reference)[:8].upper(), op.get_type_display(), op.get_service_display(),
             op.get_status_display(), op.agent.get_full_name() or op.agent.email, op.currency.code, op.amount, op.fee, op.supplier_fee] for op in operations.order_by("created_at", "id")]
    summary = operations.values("currency__code", "type").annotate(n=Count("id"), volume=Sum("amount"), fees=Sum("fee")).order_by("currency__code", "type")
    type_labels = dict(Operation._meta.get_field("type").choices)
    sections = [
        _section("Synthèse par devise et type", ["Devise", "Type", "Nombre", "Volume", "Commissions"],
                 [[r["currency__code"], type_labels[r["type"]], r["n"], _money(r["volume"]), _money(r["fees"])] for r in summary], numeric=[2, 3, 4]),
        _section("Détail des opérations", ["Date", "Référence", "Type", "Service", "Statut", "Agent", "Devise", "Montant", "Commission", "Frais fournisseur"],
                 rows, numeric=[7, 8, 9], totals=(6, [7, 8, 9])),
    ]
    if finance_policy(user).own_cash_only:
        sections.insert(0, _agent_cash_day(user, start_at, end_at))
    return sections


def _agent_cash_day(user, start_at, end_at):
    rows = []
    for account in FinancialAccount.objects.filter(responsible_user=user, account_type__in=TREASURY_TYPES).select_related("currency"):
        opening, closing = ledger_balance(account, before=start_at), ledger_balance(account, before=end_at)
        entries = LedgerEntry.objects.filter(account=account, batch__status__in=["POSTED", "REVERSED"], batch__effective_at__gte=start_at, batch__effective_at__lt=end_at)
        inflow = entries.filter(side="DEBIT").aggregate(v=Sum("amount"))["v"] or ZERO
        outflow = entries.filter(side="CREDIT").aggregate(v=Sum("amount"))["v"] or ZERO
        rows.append([account.code, account.currency.code, opening, inflow, outflow, closing])
    return _section("Ma caisse : ouverture, mouvements, clôture", ["Compte", "Devise", "Ouverture", "Entrées", "Sorties", "Clôture théorique"], rows, numeric=[2, 3, 4, 5])


def _funds(user, start_at, end_at, filters, party=None):
    contributions = FundContribution.objects.select_related("stakeholder", "currency", "receiving_account").filter(batch__status__in=["POSTED", "REVERSED"])
    if party:
        contributions = contributions.filter(stakeholder=party)
    in_period = contributions.filter(batch__effective_at__gte=start_at, batch__effective_at__lt=end_at)
    rows = [[c.received_on.strftime("%d/%m/%Y"), c.get_origin_display(), c.stakeholder.name, c.currency.code, c.amount, c.receiving_account.code, c.external_reference] for c in in_period.order_by("received_on", "id")]
    balances = []
    accounts = FinancialAccount.objects.filter(account_type__in=[AccountType.CAPITAL, AccountType.INVESTOR_FUNDS, AccountType.PARTNER_GUARANTEE], economic_owner__isnull=False).select_related("currency", "economic_owner")
    if party:
        accounts = accounts.filter(economic_owner=party)
    for account in accounts.order_by("economic_owner__name", "code"):
        opening, closing = ledger_balance(account, before=start_at), ledger_balance(account, before=end_at)
        balances.append([account.economic_owner.name, account.get_account_type_display(), account.currency.code, opening, closing - opening, closing])
    requests = StakeholderRequest.objects.select_related("stakeholder", "currency").filter(status=RequestStatus.EXECUTED, executed_at__gte=start_at, executed_at__lt=end_at)
    if party:
        requests = requests.filter(stakeholder=party)
    movements = [[timezone.localtime(r.executed_at).strftime("%d/%m/%Y"), r.stakeholder.name, r.get_kind_display(), r.currency.code, r.amount] for r in requests]
    return [
        _section("Apports reçus dans la période", ["Date", "Origine", "Partie", "Devise", "Montant", "Compte de réception", "Référence"], rows, numeric=[4], totals=(3, [4])),
        _section("Soldes par partie : début, variation, fin", ["Partie", "Nature", "Devise", "Solde début", "Variation", "Solde fin"], balances, numeric=[3, 4, 5]),
        _section("Augmentations, retraits et réinvestissements exécutés", ["Date", "Partie", "Type", "Devise", "Montant"], movements, numeric=[4]),
    ]


def _treasury(user, start_at, end_at, filters):
    rows = []
    for account in FinancialAccount.objects.filter(account_type__in=TREASURY_TYPES).select_related("currency", "service", "responsible_user").order_by("service_code", "code"):
        opening, closing = ledger_balance(account, before=start_at), ledger_balance(account, before=end_at)
        entries = LedgerEntry.objects.filter(account=account, batch__status__in=["POSTED", "REVERSED"], batch__effective_at__gte=start_at, batch__effective_at__lt=end_at)
        inflow = entries.filter(side="DEBIT").aggregate(v=Sum("amount"))["v"] or ZERO
        outflow = entries.filter(side="CREDIT").aggregate(v=Sum("amount"))["v"] or ZERO
        count = account.accountcount_set.filter(period__end_date__gte=timezone.localtime(end_at - timedelta(seconds=1)).date(), declared__isnull=False).order_by("-recorded_at").first()
        declared = count.declared if count else None
        rows.append([account.code, account.service.name if account.service else account.get_account_type_display(), account.currency.code, opening, inflow, outflow,
                     opening + inflow - outflow, declared if declared is not None else "non compté", (declared - closing) if declared is not None else "", count.justification if count else ""])
    transit = [[t.initiated_at.strftime("%d/%m/%Y"), t.source.code, t.destination.code, t.currency.code, t.amount, t.fee, t.get_status_display()]
               for t in InternalTransfer.objects.select_related("source", "destination", "currency").filter(initiated_at__lt=end_at).exclude(status="CANCELLED").filter(initiated_at__gte=start_at)]
    in_flight = [[c.code.split("-")[1], -ledger_balance(c, before=end_at)] for c in FinancialAccount.objects.filter(account_type=AccountType.TRANSIT)]
    from .models import CurrencyConversion
    conversions = [[timezone.localtime(c.created_at).strftime("%d/%m/%Y"), c.source.code, c.amount_source, c.source.currency.code, c.destination.code, c.amount_destination, c.destination.currency.code,
                    c.rate_applied, c.reference_rate if c.reference_rate is not None else "non publié", c.fee_source, c.realized_difference]
                   for c in CurrencyConversion.objects.filter(created_at__gte=start_at, created_at__lt=end_at).select_related("source__currency", "destination__currency")]
    return [
        _section("Rapprochement par caisse et compte", ["Compte", "Service", "Devise", "Ouverture", "Entrées", "Sorties", "Clôture théorique", "Solde constaté", "Écart", "Explication"], rows, numeric=[3, 4, 5, 6, 7, 8],
                 totals=(2, [3, 4, 5, 6])),
        _section("Fonds en transit à la fin de période", ["Devise", "Montant en transit"], in_flight, numeric=[1]),
        _section("Transferts internes de la période", ["Date", "Source", "Destination", "Devise", "Montant", "Frais", "Statut"], transit, numeric=[4, 5]),
        _section("Conversions de devises (deux montants, taux, frais, écart réalisé)", ["Date", "Source", "Montant source", "Devise", "Destination", "Montant reçu", "Devise", "Taux appliqué", "Taux de référence", "Frais", "Écart réalisé"],
                 conversions, numeric=[2, 5, 7, 8, 9, 10]),
    ]


def _commissions(user, start_at, end_at, filters):
    from apps.operations.models import Operation
    from apps.stakeholders.models import PartnerOperation
    operations = Operation.objects.filter(status="COMPLETED", created_at__gte=start_at, created_at__lt=end_at).select_related("currency", "agent")
    attributed = PartnerOperation.objects.filter(operation__in=operations).select_related("stakeholder", "operation__currency", "operation__agent")
    partner_ids = set(attributed.values_list("operation_id", flat=True))
    own = [[op.agent.get_full_name() or op.agent.email, op.get_service_display(), op.currency.code, op.fee] for op in operations.exclude(pk__in=partner_ids)]
    partners = [[a.stakeholder.name, a.operation.agent.get_full_name() or a.operation.agent.email, a.operation.get_service_display(), a.operation.currency.code, a.operation.fee,
                 a.share_percent, (a.operation.fee * a.share_percent / 100).quantize(Decimal("0.01")), a.operation.fee - (a.operation.fee * a.share_percent / 100).quantize(Decimal("0.01"))] for a in attributed]
    return [
        _section("Commissions propres FINCORYA", ["Agent", "Service", "Devise", "Commission"], own, numeric=[3], totals=(2, [3])),
        _section("Commissions partenaires : brut, part partenaire, part FINCORYA", ["Partenaire", "Agent", "Service", "Devise", "Brut", "Part %", "Part partenaire", "Part FINCORYA"], partners,
                 numeric=[4, 5, 6, 7], totals=(3, [4, 6, 7])),
    ]


def _expenses(user, start_at, end_at, filters):
    from apps.expenses.models import Expense
    rows = []
    for expense in Expense.objects.select_related("currency", "agent", "stakeholder").filter(incurred_on__gte=start_at.date(), incurred_on__lt=end_at.date()).order_by("incurred_on"):
        paid = expense.payments.aggregate(v=Sum("amount"))["v"] or ZERO
        rows.append([expense.incurred_on.strftime("%d/%m/%Y"), expense.get_category_display(), expense.label, expense.currency.code, expense.amount, paid, expense.amount - paid,
                     expense.get_status_display(), "oui" if expense.receipt else "non"])
    return [_section("Charges : salaires, frais fournisseurs, autres dépenses", ["Date", "Catégorie", "Libellé", "Devise", "Montant", "Payé", "Reste dû", "Statut", "Justificatif"], rows,
                     numeric=[4, 5, 6], totals=(3, [4, 5, 6]))]


def _investors(user, start_at, end_at, filters, party=None):
    from apps.stakeholders.models import Stakeholder
    from apps.expenses.models import Expense
    parties = Stakeholder.objects.filter(type="INVESTOR", is_active=True)
    if party:
        parties = parties.filter(pk=party.pk)
    rows, returns = [], []
    for investor in parties.order_by("name"):
        for account in FinancialAccount.objects.filter(economic_owner=investor, account_type=AccountType.INVESTOR_FUNDS).select_related("currency"):
            rows.append([investor.name, account.currency.code, ledger_balance(account, before=end_at), investor.investor_return_percent, investor.get_payment_frequency_display()])
        for expense in Expense.objects.filter(stakeholder=investor, category="INVESTOR_RETURN", incurred_on__gte=start_at.date(), incurred_on__lt=end_at.date()).select_related("currency"):
            paid = expense.payments.aggregate(v=Sum("amount"))["v"] or ZERO
            returns.append([investor.name, expense.incurred_on.strftime("%d/%m/%Y"), expense.currency.code, expense.amount, paid, expense.get_status_display()])
    requests = StakeholderRequest.objects.filter(stakeholder__in=parties, submitted_at__gte=start_at, submitted_at__lt=end_at).select_related("stakeholder", "currency")
    return [
        _section("Investissements en place", ["Investisseur", "Devise", "Capital investi", "Rendement %", "Fréquence"], rows, numeric=[2, 3]),
        _section("Rémunérations dues et payées", ["Investisseur", "Date", "Devise", "Due", "Payée", "Statut"], returns, numeric=[3, 4]),
        _section("Demandes", ["Investisseur", "Type", "Devise", "Montant", "Statut"], [[r.stakeholder.name, r.get_kind_display(), r.currency.code, r.amount, r.get_status_display()] for r in requests], numeric=[3]),
    ]


def _partners(user, start_at, end_at, filters, party=None):
    from apps.stakeholders.models import PartnerOperation, Stakeholder
    parties = Stakeholder.objects.filter(type="PARTNER", is_active=True)
    if party:
        parties = parties.filter(pk=party.pk)
    rows, activity = [], []
    for partner in parties.order_by("name"):
        guarantee = getattr(partner, "guarantee", None)
        for account in FinancialAccount.objects.filter(economic_owner=partner, account_type__in=[AccountType.PARTNER_GUARANTEE, AccountType.PARTNER_PAYABLE]).select_related("currency"):
            rows.append([partner.name, account.get_account_type_display(), account.currency.code, ledger_balance(account, before=end_at),
                         guarantee.per_operation_ceiling if guarantee and account.account_type == AccountType.PARTNER_GUARANTEE else ""])
        attributed = PartnerOperation.objects.filter(stakeholder=partner, operation__status="COMPLETED", operation__created_at__gte=start_at, operation__created_at__lt=end_at).select_related("operation__currency")
        for code in sorted(set(attributed.values_list("operation__currency__code", flat=True))):
            subset = [a for a in attributed if a.operation.currency.code == code]
            gross = sum((a.operation.fee for a in subset), ZERO)
            share = sum(((a.operation.fee * a.share_percent / 100).quantize(Decimal("0.01")) for a in subset), ZERO)
            activity.append([partner.name, code, len(subset), sum((a.operation.amount for a in subset), ZERO), gross, share, gross - share])
    settlements = [[timezone.localtime(e.batch.effective_at).strftime("%d/%m/%Y"), e.account.economic_owner.name, e.currency.code, e.amount, e.batch.event_type]
                   for e in LedgerEntry.objects.filter(account__economic_owner__in=parties, account__account_type=AccountType.PARTNER_PAYABLE, side="DEBIT", batch__status__in=["POSTED", "REVERSED"],
                                                       batch__effective_at__gte=start_at, batch__effective_at__lt=end_at).select_related("batch", "currency", "account__economic_owner")]
    return [
        _section("Garantie, commissions dues et plafond par opération", ["Partenaire", "Position", "Devise", "Solde", "Plafond / opération"], rows, numeric=[3, 4]),
        _section("Activité et commissions de la période", ["Partenaire", "Devise", "Opérations", "Volume", "Commission brute", "Part partenaire", "Part FINCORYA"], activity, numeric=[2, 3, 4, 5, 6]),
        _section("Règlements et conversions en garantie", ["Date", "Partenaire", "Devise", "Montant", "Nature"], settlements, numeric=[3]),
    ]


def _shareholders(user, start_at, end_at, filters, party=None):
    from apps.profits.models import Distribution
    from apps.stakeholders.models import Stakeholder
    parties = Stakeholder.objects.filter(type="SHAREHOLDER", is_active=True)
    if party:
        parties = parties.filter(pk=party.pk)
    capital, distributions = [], []
    for holder in parties.order_by("name"):
        for account in FinancialAccount.objects.filter(economic_owner=holder, account_type=AccountType.CAPITAL).select_related("currency"):
            total = sum((ledger_balance(a, before=end_at) for a in FinancialAccount.objects.filter(account_type=AccountType.CAPITAL, currency=account.currency, economic_owner__isnull=False)), ZERO)
            balance = ledger_balance(account, before=end_at)
            capital.append([holder.name, account.currency.code, balance, (balance / total * 100).quantize(Decimal("0.01")) if total else ""])
    for row in Distribution.objects.filter(stakeholder__in=parties, approval_batch__isnull=False, allocation__period__end_date__gte=start_at.date(), allocation__period__start_date__lt=end_at.date()).select_related("stakeholder", "allocation__period__currency"):
        plan = row.allocation.period.snapshot.get("distribution_plan", {})
        distributions.append([row.stakeholder.name, f"{row.allocation.period.start_date:%m/%Y}", row.allocation.period.currency.code, row.amount, plan.get("mode", "—"), row.get_status_display()])
    return [
        _section("Apports et participation au capital", ["Actionnaire", "Devise", "Capital apporté", "Participation %"], capital, numeric=[2, 3]),
        _section("Bénéfices attribués, paiements et réinvestissements", ["Actionnaire", "Mois", "Devise", "Montant", "Mode de distribution", "Statut"], distributions, numeric=[3]),
    ]


def _result(user, start_at, end_at, filters):
    from apps.profits.models import ProfitPeriod
    rows, fx_rows, plans = [], [], []
    for profit in ProfitPeriod.objects.filter(finance_period__isnull=False, start_date__gte=start_at.date(), end_date__lt=end_at.date()).select_related("currency").order_by("start_date"):
        result = profit.snapshot.get("result", {})
        rows.append([f"{profit.start_date:%m/%Y}", profit.currency.code, *[_money(result.get(k)) for k in ("own_commissions", "fincorya_share", "salaries", "other_expenses", "supplier_fees", "investor_returns")],
                     profit.net_profit, profit.prior_losses, profit.distributable, profit.proposed_distribution])
        fx_rows.append([f"{profit.start_date:%m/%Y}", profit.currency.code, profit.net_profit, _money(result.get("fx_realized")), _money(result.get("fx_revaluation")), profit.distributable,
                        result.get("fx_note", "Écarts de change exclus de la distribution ; traitement à définir.")])
        for dist in profit.allocations.prefetch_related("distributions__stakeholder"):
            for share in dist.distributions.all():
                plans.append([f"{profit.start_date:%m/%Y}", share.stakeholder.name, profit.currency.code, share.amount, share.get_status_display()])
    return [
        _section("Résultat mensuel selon les règles FINCORYA", ["Mois", "Devise", "Commissions propres", "Part FINCORYA partenaires", "Salaires", "Autres charges", "Frais fournisseurs", "Rémunérations investisseurs", "Résultat net", "Pertes reportées", "Distribuable", "Distribué"],
                 rows, numeric=list(range(2, 12))),
        _section("Écarts de change (hors distribution)", ["Mois", "Devise", "Résultat FINCORYA", "Écarts réalisés", "Réévaluations", "Distribuable effectif", "Mention"], fx_rows, numeric=[2, 3, 4, 5]),
        _section("Distribution aux actionnaires", ["Mois", "Actionnaire", "Devise", "Part", "Statut"], plans, numeric=[3]),
    ]


def _control(user, start_at, end_at, filters):
    from apps.operations.models import Operation
    from .services import reconcile_account
    from apps.audit.models import AuditEvent
    reversals = [[timezone.localtime(b.effective_at).strftime("%d/%m/%Y %H:%M"), b.description, b.event_type, b.posted_by.email if b.posted_by else ""]
                 for b in JournalBatch.objects.filter(event_type="REVERSAL", effective_at__gte=start_at, effective_at__lt=end_at).select_related("posted_by")]
    pending = [[str(op.reference)[:8].upper(), op.get_type_display(), op.currency.code, op.amount, op.get_status_display()] for op in Operation.objects.filter(status="PENDING", created_at__lt=end_at).select_related("currency")]
    drift = [[a.code, a.currency.code, a.cached_balance, reconcile_account(a)["ledger"]] for a in FinancialAccount.objects.select_related("currency") if reconcile_account(a)["difference"]]
    requests = [[r.stakeholder.name, r.get_kind_display(), r.currency.code, r.amount, r.get_status_display()] for r in StakeholderRequest.objects.filter(status__in=["SUBMITTED", "APPROVED"]).select_related("stakeholder", "currency")]
    validations = [[timezone.localtime(a.created_at).strftime("%d/%m/%Y %H:%M"), a.actor.email if a.actor else "système", a.action] for a in AuditEvent.objects.filter(created_at__gte=start_at, created_at__lt=end_at,
                    action__in=["JOURNAL_POST", "FINANCE_PERIOD_LOCK", "FINANCE_DISTRIBUTION_APPROVE", "REQUEST_DECIDE", "REQUEST_EXECUTE", "TRANSFER_CONFIRM", "FUND_CONTRIBUTION"]).select_related("actor").order_by("-created_at")[:200]]
    return [
        _section("Corrections et annulations (contre-passations)", ["Date", "Description", "Type", "Validé par"], reversals),
        _section("Opérations en attente", ["Référence", "Type", "Devise", "Montant", "Statut"], pending, numeric=[3]),
        _section("Écarts cache / grand livre", ["Compte", "Devise", "Cache", "Grand livre"], drift, numeric=[2, 3]),
        _section("Demandes en attente de validation ou d’exécution", ["Partie", "Type", "Devise", "Montant", "Statut"], requests, numeric=[3]),
        _section("Historique des validations", ["Date", "Auteur", "Action"], validations),
    ]


# --------------------------------------------------------------------------- entry point

def build_report(*, user, kind, preset, anchor, custom_end=None, filters=None):
    filters = filters or {}
    if kind not in allowed_kinds(user):
        raise PermissionDenied("Ce rapport n’est pas disponible pour votre rôle.")
    party = linked_party(user)
    if party and kind == PARTY_KIND.get(party.type):
        filters = {"party": party.name}
    elif party and not finance_policy(user).global_read_only:
        raise PermissionDenied("Ce rapport n’est pas disponible pour votre rôle.")
    if finance_policy(user).own_cash_only and (preset != "DAY" or anchor > timezone.localdate()):
        raise PermissionDenied("Un agent consulte une journée passée ou en cours.")
    start, end, label = period_bounds(preset, anchor, custom_end)
    start_at, end_at = business_day_bounds(start, end)
    builders = {"ACTIVITY": _activity, "FUNDS": _funds, "TREASURY": _treasury, "COMMISSIONS": _commissions, "EXPENSES": _expenses,
                "INVESTORS": _investors, "PARTNERS": _partners, "SHAREHOLDERS": _shareholders, "RESULT": _result, "CONTROL": _control}
    if kind in {"FUNDS", "INVESTORS", "PARTNERS", "SHAREHOLDERS"}:
        sections = builders[kind](user, start_at, end_at, filters, party=party if party and kind == PARTY_KIND.get(party.type) else None)
    else:
        sections = builders[kind](user, start_at, end_at, filters)
    generated = timezone.now()
    return {
        "id": uuid.uuid4().hex[:12].upper(), "version": 1, "kind": kind, "title": REPORT_KINDS[kind],
        "period": {"start": start, "end": end, "label": label, "timezone": settings.BUSINESS_TIME_ZONE, "preset": preset},
        "week_convention": f"Semaine du {WEEKDAYS[week_start()]} au {WEEKDAYS[(week_start() + 6) % 7]} ({settings.BUSINESS_TIME_ZONE})",
        "filters": {k: str(v) for k, v in filters.items() if v}, "currency_note": "Montants présentés par devise, sans consolidation ni conversion.",
        "generated_at": generated, "author": user.get_full_name() or user.email, "status": _status(start, end),
        "sections": sections,
    }


def header_rows(report):
    return [
        ["FINCORYA", report["title"]], ["Période", f"{report['period']['label']} · {report['period']['timezone']}"],
        ["Convention de semaine", report["week_convention"]], ["Filtres", ", ".join(f"{k}: {v}" for k, v in report["filters"].items()) or "aucun"],
        ["Devise", report["currency_note"]], ["Généré le", timezone.localtime(report["generated_at"]).strftime("%d/%m/%Y %H:%M")],
        ["Auteur", report["author"]], ["Statut", report["status"]], ["Identifiant / version", f"{report['id']} · v{report['version']}"],
    ]


def safe_cell(value):
    """Neutralise spreadsheet formula injection for text cells."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value
