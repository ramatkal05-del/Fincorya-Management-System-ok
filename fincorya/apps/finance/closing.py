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
    from .models import RemunerationTerms
    from .results import compute_result
    days_in_month = (period.end_date - period.start_date).days + 1
    for party in Stakeholder.objects.filter(pk__in=party_ids).order_by("pk"):
        rule = effective_rule(party, RuleKind.REMUNERATION, period.end_date)
        changes = EconomicRule.objects.filter(stakeholder=party, kind=RuleKind.REMUNERATION)
        if (changes.filter(effective_from__gt=period.start_date, effective_from__lte=period.end_date).exists()
                or changes.filter(effective_to__gte=period.start_date, effective_to__lt=period.end_date).exists()) and not (rule and rule.effective_from > period.start_date):
            raise ValidationError(f"{party.name} : rémunération modifiée en cours de mois, montant à confirmer.")
        if not rule or rule.value == 0:
            continue
        terms = RemunerationTerms.objects.filter(stakeholder=party).first()
        amount, basis = rule.value, f"Règle datée #{rule.pk}"
        # Contract window: nothing is due outside the contract dates.
        if terms and terms.contract_start and terms.contract_start > period.end_date:
            continue
        if terms and terms.contract_end and terms.contract_end < period.start_date:
            continue
        covered_start = max([period.start_date, rule.effective_from] + [d for d in [terms.contract_start if terms else None] if d])
        covered_end = min([period.end_date] + [d for d in [rule.effective_to, terms.contract_end if terms else None] if d])
        covered_days = (covered_end - covered_start).days + 1
        if covered_days < days_in_month:
            partial = terms.partial_month if terms else ""
            if not partial:
                raise ValidationError(f"{party.name} : mois incomplet ({covered_days} jours) ; définissez la règle contractuelle de mois incomplet (prorata, mois complet ou rien).")
            if partial == "NONE":
                continue
            if partial == "PRORATA":
                amount = (rule.value * covered_days / days_in_month).quantize(Decimal("0.01"))
                basis += f" · prorata {covered_days}/{days_in_month} jours"
        if party.type == "INVESTOR":
            preliminary = compute_result(period, rule.currency)
            if preliminary["net_result"] - preliminary["prior_losses"] < amount:
                loss_rule = terms.loss_month if terms else ""
                if not loss_rule:
                    raise ValidationError(f"{party.name} : mois déficitaire ; définissez la règle contractuelle (rémunération due ou suspendue).")
                if loss_rule == "SUSPEND":
                    continue
        key = f"remuneration-{party.pk}-{period.start_date:%Y-%m}"
        expense, _ = Expense.objects.get_or_create(accrual_key=key, defaults={
            "label": f"Rémunération {party.name} {period.start_date:%Y-%m}", "category": "INVESTOR_RETURN" if party.type == "INVESTOR" else "OTHER",
            "amount": amount, "currency": rule.currency, "incurred_on": period.end_date,
            "stakeholder": party, "created_by": actor})
        if expense.status == "PENDING":
            decide_expense(expense_id=expense.pk, actor=actor, decision="APPROVED", comment=basis)


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
        from .results import compute_result, distribution_plan
        plan = distribution_plan(period)
        batch_ids = list(JournalBatch.objects.filter(status__in=["POSTED", "REVERSED"], effective_at__gte=start, effective_at__lt=end).values_list("pk", flat=True))
        for code in report["totals"]:
            currency = Currency.objects.get(code=code)
            result = compute_result(period, currency)
            profit, created = ProfitPeriod.objects.get_or_create(start_date=period.start_date, end_date=period.end_date,
                currency=currency, defaults={"finance_period": period})
            if not created:
                raise ValidationError("Une période de bénéfice existe déjà ; aucune ancienne distribution ne sera recalculée.")
            profit.gross_fees, profit.deductible_expenses, profit.net_profit = result["income"], result["total_expenses"], result["net_result"]
            profit.prior_losses, profit.distributable = result["prior_losses"], result["distributable"]
            profit.status, profit.calculated_by, profit.calculated_at = ProfitStatus.FINALIZED, actor, timezone.now()
            profit.snapshot = {"engine": "finance", "result": {k: str(v) for k, v in result.items()}, "distribution_plan": plan, "batch_ids": batch_ids}
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
    if profit.distributable <= 0:
        raise ValidationError("Résultat nul ou perte : aucune distribution, la perte est reportée.")
    if amount <= 0 or amount > profit.distributable:
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
    if amount <= 0 or amount > profit.distributable:
        raise ValidationError("La proposition dépasse le bénéfice distribuable.")
    if profit.allocations.exists():
        if profit.proposed_distribution == amount:
            return profit
        raise ValidationError("Une distribution est déjà approuvée.")
    from .results import split_amount
    plan = profit.snapshot.get("distribution_plan")
    if not plan or not plan.get("weights"):
        raise ValidationError("Le plan de distribution figé à la clôture est absent ; période à qualifier.")
    allocation = Allocation.objects.create(period=profit, bucket="FINANCE_DIVIDENDS", percentage=100, amount=amount)
    for stakeholder_id, part in split_amount(amount, plan["weights"]):
        party = Stakeholder.objects.get(pk=stakeholder_id)
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
    from .funds import distribution_remaining
    from .models import RequestStatus
    if row.requests.filter(status__in=[RequestStatus.SUBMITTED, RequestStatus.APPROVED]).exists():
        raise ValidationError("Une demande de l’actionnaire est en cours sur cette distribution.")
    amount = distribution_remaining(row)
    if amount <= 0:
        raise ValidationError("Cette distribution a déjà été entièrement réglée par des demandes exécutées.")
    currency = row.allocation.period.currency
    payable = counterpart(currency, AccountType.DISTRIBUTION_PAYABLE, party=row.stakeholder)
    if destination == "REINVEST":
        row.payment_batch = record_event(actor=actor, source=row, event="DISTRIBUTION_REINVEST", effective_at=timezone.now(), lines=[
            line(payable, "DEBIT", amount), line(counterpart(currency, AccountType.CAPITAL, party=row.stakeholder), "CREDIT", amount)])
        row.status = DistributionStatus.REINVESTED
    else:
        if account_id is None:
            raise ValidationError("Un compte de paiement est requis.")
        cash = FinancialAccount.objects.get(pk=account_id)
        _validate_cash_payment(cash, currency.pk, amount)
        row.payment_batch = record_event(actor=actor, source=row, event="DISTRIBUTION_PAY", effective_at=timezone.now(), lines=[
            line(payable, "DEBIT", amount), line(cash, "CREDIT", amount)])
        _project_payment(cash, amount, actor, f"Distribution #{row.pk}")
        row.status = DistributionStatus.PAID
    row.paid_at = timezone.now()
    row.save(update_fields=["payment_batch", "status", "paid_at"])
    return row
