import calendar
from decimal import Decimal

from django.core.exceptions import ValidationError


from django.utils import timezone

from apps.accounts.permissions import require_finance_access
from apps.audit.services import record
from config.business_time import business_day_bounds
from .cutover import active_cutover
from .events import counterpart, effective_rule, line, record_event, _validate_cash_payment, _project_payment
from .locking import ledger_atomic, save_internal, monthly_close
from .models import (AccountCount, AccountNature, AccountType, EconomicRule, FinancialAccount,
                     FinancialPeriod, ImportRow, JournalBatch, PeriodStatus, RuleKind)
from .services import ledger_balance, reconcile_account

CASH_TYPES = ["AGENT_CASH", "GLOBAL_CASH", "MOBILE_MONEY", "DIGITAL", "FINANCIAL_SERVICE"]


@ledger_atomic
def record_count(*, period_id, account_id, declared, justification, actor):
    require_finance_access(actor, "prepare")
    period = FinancialPeriod.objects.get(pk=period_id)
    if period.status == PeriodStatus.LOCKED:
        raise ValidationError("Période verrouillée.")
    account = FinancialAccount.objects.get(pk=account_id, account_type__in=CASH_TYPES)
    _, end = business_day_bounds(period.start_date, period.end_date)
    theoretical = ledger_balance(account, before=end)
    if declared is not None and declared < 0:
        raise ValidationError("Un comptage ne peut pas être négatif.")
    if declared is not None and declared != theoretical and not justification.strip():
        raise ValidationError("L’écart de comptage exige une justification.")
    count, _ = AccountCount.objects.update_or_create(period=period, account=account, defaults={
        "theoretical": theoretical, "declared": declared, "justification": justification,
        "recorded_by": actor})
    record(actor=actor, action="FINANCE_COUNT", instance=count,
           after={"theoretical": str(theoretical), "declared": str(declared), "justification": justification})
    return count


def period_report(period):
    start, end = business_day_bounds(period.start_date, period.end_date)
    accounts = FinancialAccount.objects.select_related("currency", "economic_owner")
    totals = {}
    controls = []
    counts = {row.account_id: row for row in period.counts.all()}
    rows = []
    for account in accounts:
        opening = ledger_balance(account, before=start)
        closing = ledger_balance(account, before=end)
        delta = closing - opening
        total = totals.setdefault(account.currency.code, {"income": Decimal("0"), "expenses": Decimal("0"), "result": Decimal("0"), "cash": Decimal("0")})
        if account.nature == AccountNature.INCOME:
            total["income"] += delta
        if account.nature == AccountNature.EXPENSE:
            total["expenses"] += delta
        if account.account_type in CASH_TYPES:
            total["cash"] += closing
        if account.account_type in CASH_TYPES and (account.is_active or closing != 0):
            count = counts.get(account.pk)
            if not count or count.declared is None:
                controls.append(f"{account.code} : compte non compté")
            elif count.theoretical != closing:
                controls.append(f"{account.code} : comptage périmé, nouvelles écritures")
            elif count.declared != closing:
                controls.append(f"{account.code} : écart déclaré {count.declared - closing} — {count.justification}")
        if reconcile_account(account)["difference"]:
            controls.append(f"{account.code} : cache différent du grand livre")
        rows.append({"account": account, "opening": opening, "closing": closing, "delta": delta, "count": counts.get(account.pk)})
    for total in totals.values():
        total["result"] = total["income"] - total["expenses"]
    from apps.operations.models import Operation
    from apps.expenses.models import Expense
    missing = Operation.objects.filter(status="COMPLETED", created_at__gte=start, created_at__lt=end, commission_owner_confirmed=False).count()
    if missing:
        controls.append(f"{missing} opération(s) : attribution des commissions non confirmée")
    unresolved = ImportRow.objects.exclude(anomalies=[]).filter(resolution="").count()
    if unresolved:
        controls.append(f"{unresolved} ligne(s) de préparation d’import non résolue(s)")
    if Expense.objects.filter(incurred_on__range=(period.start_date, period.end_date), status="PENDING").exists():
        controls.append("Charges en attente de décision")
    if JournalBatch.objects.filter(status="DRAFT", effective_at__gte=start, effective_at__lt=end).exists():
        controls.append("Lots brouillons dans la période")
    return {"period": period, "totals": totals, "accounts": rows, "controls": controls}


@ledger_atomic
def accrue_remunerations(period, actor):
    from apps.expenses.models import Expense
    from apps.expenses.services import decide_expense
    if period.period_type != "MONTH":
        return
    if period.start_date.day != 1 or period.end_date.day != calendar.monthrange(period.end_date.year, period.end_date.month)[1] or period.start_date.replace(day=1) != period.end_date.replace(day=1):
        raise ValidationError("La clôture mensuelle couvre exactement un mois civil.")
    party_ids = EconomicRule.objects.filter(kind=RuleKind.REMUNERATION).values_list("stakeholder_id", flat=True).distinct()
    from apps.stakeholders.models import Stakeholder
    for party in Stakeholder.objects.filter(pk__in=party_ids):
        changes = EconomicRule.objects.filter(stakeholder=party, kind=RuleKind.REMUNERATION)
        if (changes.filter(effective_from__gt=period.start_date, effective_from__lte=period.end_date).exists()
                or changes.filter(effective_to__gte=period.start_date, effective_to__lt=period.end_date).exists()):
            raise ValidationError(f"{party.name} : rémunération modifiée en cours de mois, montant à confirmer.")
        rule = effective_rule(party, RuleKind.REMUNERATION, period.end_date)
        if not rule or rule.value == 0:
            continue
        if rule.effective_from > period.start_date:
            raise ValidationError(f"{party.name} : rémunération débutant en cours de mois, montant à confirmer.")
        key = f"remuneration-{party.pk}-{period.start_date:%Y-%m}"
        expense, _ = Expense.objects.get_or_create(accrual_key=key, defaults={
            "label": f"Rémunération {party.name} {period.start_date:%Y-%m}", "category": "OTHER",
            "amount": rule.value, "currency": rule.currency, "incurred_on": period.end_date,
            "stakeholder": party, "created_by": actor})
        if expense.status == "PENDING":
            decide_expense(expense_id=expense.pk, actor=actor, decision="APPROVED", comment=f"Règle datée #{rule.pk}")


@ledger_atomic
def close_period(*, period_id, actor):
    require_finance_access(actor, "approve")
    run = active_cutover()
    if not run:
        raise ValidationError("La bascule doit être approuvée avant la clôture.")
    period = FinancialPeriod.objects.get(pk=period_id)
    if period.status == PeriodStatus.LOCKED:
        return period
    start, end = business_day_bounds(period.start_date, period.end_date)
    if end > timezone.now():
        raise ValidationError("La période n’est pas terminée.")
    if start < run.cutover_at:
        raise ValidationError("Cette période chevauche la bascule ; reprise spécifique requise.")
    token = monthly_close.set(period if period.period_type == "MONTH" else None)
    try:
        accrue_remunerations(period, actor)
    finally:
        monthly_close.reset(token)
    report = period_report(period)
    if report["controls"]:
        raise ValidationError(report["controls"])
    if period.period_type == "MONTH":
        from apps.profits.models import ProfitPeriod, ProfitStatus
        from apps.pricing.models import Currency
        from apps.stakeholders.models import Stakeholder
        rules = []
        for party in Stakeholder.objects.filter(financial_rules__kind=RuleKind.DIVIDEND).distinct():
            rule = effective_rule(party, RuleKind.DIVIDEND, period.end_date)
            if rule:
                if rule.effective_from > period.start_date:
                    raise ValidationError("Une règle de distribution change en cours de mois ; période à qualifier.")
                rules.append({"stakeholder_id": party.pk, "rule_id": rule.pk, "percent": str(rule.value)})
        for code, total in report["totals"].items():
            profit, created = ProfitPeriod.objects.get_or_create(start_date=period.start_date, end_date=period.end_date,
                currency=Currency.objects.get(code=code), defaults={"finance_period": period})
            if not created:
                raise ValidationError("Une période de bénéfice existe déjà ; aucune ancienne distribution ne sera recalculée.")
            profit.gross_fees, profit.deductible_expenses, profit.net_profit = total["income"], total["expenses"], total["result"]
            profit.status, profit.calculated_by, profit.calculated_at = ProfitStatus.FINALIZED, actor, timezone.now()
            profit.snapshot = {"engine": "finance", "cash": str(total["cash"]), "dividend_rules": rules,
                               "batch_ids": list(JournalBatch.objects.filter(status__in=["POSTED", "REVERSED"], effective_at__gte=start, effective_at__lt=end).values_list("pk", flat=True))}
            profit.save()
    period.status, period.locked_at, period.locked_by = PeriodStatus.LOCKED, timezone.now(), actor
    save_internal(period)
    record(actor=actor, action="FINANCE_PERIOD_LOCK", instance=period, after={"controls": []})
    return period


@ledger_atomic
def propose_distribution(*, profit_id, amount, actor):
    require_finance_access(actor, "prepare")
    from apps.profits.models import ProfitPeriod
    profit = ProfitPeriod.objects.select_for_update().get(pk=profit_id)
    if not active_cutover() or not profit.finance_period_id or profit.finance_period.status != PeriodStatus.LOCKED:
        raise ValidationError("Une clôture finance est requise.")
    if profit.allocations.exists():
        raise ValidationError("La distribution est déjà approuvée.")
    if amount <= 0 or amount > max(Decimal("0"), profit.net_profit):
        raise ValidationError("La proposition dépasse le bénéfice distribuable.")
    profit.proposed_distribution = amount
    profit.proposed_by = actor
    profit.proposed_at = timezone.now()
    profit.save(update_fields=["proposed_distribution", "proposed_by", "proposed_at"])
    record(actor=actor, action="FINANCE_DISTRIBUTION_PROPOSE", instance=profit, after={"amount": str(amount)})
    return profit


@ledger_atomic
def approve_distributions(*, profit_id, amount, actor):
    require_finance_access(actor, "approve")
    from apps.profits.models import Allocation, Distribution, ProfitPeriod
    from apps.stakeholders.models import Stakeholder
    profit = ProfitPeriod.objects.select_for_update().get(pk=profit_id)
    if not active_cutover() or not profit.finance_period_id or profit.finance_period.status != PeriodStatus.LOCKED:
        raise ValidationError("Une clôture finance est requise.")
    if not profit.proposed_at or profit.proposed_distribution != amount:
        raise ValidationError("Une proposition enregistrée correspondant au montant approuvé est requise.")
    if amount <= 0 or amount > max(Decimal("0"), profit.net_profit):
        raise ValidationError("La proposition dépasse le bénéfice distribuable.")
    if profit.allocations.exists():
        if profit.proposed_distribution == amount:
            return profit
        raise ValidationError("Une distribution est déjà approuvée.")
    rules = profit.snapshot.get("dividend_rules", [])
    if len(rules) != 4 or any(Decimal(row["percent"]) != 25 for row in rules):
        raise ValidationError("Quatre actionnaires avec des règles datées de 25 % sont requis.")
    allocation = Allocation.objects.create(period=profit, bucket="FINANCE_DIVIDENDS", percentage=100, amount=amount)
    remaining = amount
    for index, row in enumerate(rules):
        part = remaining if index == 3 else (amount / 4).quantize(Decimal("0.01"))
        remaining -= part
        party = Stakeholder.objects.get(pk=row["stakeholder_id"])
        distribution = Distribution.objects.create(allocation=allocation, stakeholder=party, amount=part)
        if part:
            distribution.approval_batch = record_event(actor=actor, source=distribution, event="DISTRIBUTION_APPROVE", effective_at=timezone.now(),
                lines=[line(counterpart(profit.currency, AccountType.CAPITAL), "DEBIT", part),
                       line(counterpart(profit.currency, AccountType.DISTRIBUTION_PAYABLE, party=party), "CREDIT", part)])
            distribution.save(update_fields=["approval_batch"])
    profit.proposed_distribution = amount
    profit.save(update_fields=["proposed_distribution"])
    record(actor=actor, action="FINANCE_DISTRIBUTION_APPROVE", instance=profit, after={"amount": str(amount)})
    return profit


@ledger_atomic
def pay_distribution(*, distribution_id, account_id=None, destination="PAYOUT", actor):
    require_finance_access(actor, "approve")
    from apps.profits.models import Distribution, DistributionStatus
    row = Distribution.objects.select_for_update().get(pk=distribution_id)
    if row.payment_batch_id:
        return row
    if not active_cutover() or not row.approval_batch_id:
        raise ValidationError("Distribution non approuvée dans le nouveau registre.")
    if destination not in {"PAYOUT", "REINVEST"}:
        raise ValidationError("Destination de distribution inconnue.")
    currency = row.allocation.period.currency
    payable = counterpart(currency, AccountType.DISTRIBUTION_PAYABLE, party=row.stakeholder)
    if destination == "REINVEST":
        row.payment_batch = record_event(actor=actor, source=row, event="DISTRIBUTION_REINVEST", effective_at=timezone.now(), lines=[
            line(payable, "DEBIT", row.amount), line(counterpart(currency, AccountType.CAPITAL, party=row.stakeholder), "CREDIT", row.amount)])
        row.status = DistributionStatus.REINVESTED
    else:
        if account_id is None:
            raise ValidationError("Un compte de paiement est requis.")
        cash = FinancialAccount.objects.get(pk=account_id)
        _validate_cash_payment(cash, currency.pk, row.amount)
        row.payment_batch = record_event(actor=actor, source=row, event="DISTRIBUTION_PAY", effective_at=timezone.now(), lines=[
            line(payable, "DEBIT", row.amount), line(cash, "CREDIT", row.amount)])
        _project_payment(cash, row.amount, actor, f"Distribution #{row.pk}")
        row.status = DistributionStatus.PAID
    row.paid_at = timezone.now()
    row.save(update_fields=["payment_batch", "status", "paid_at"])
    return row
