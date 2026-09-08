from decimal import Decimal, ROUND_HALF_UP
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from apps.audit.services import record
from apps.accounts.models import Role, User
from apps.expenses.models import Expense, ExpenseStatus
from apps.operations.models import Operation, OperationStatus


from apps.stakeholders.models import Investment, StakeholderType
from config.business_time import business_day_bounds
from .models import Allocation, Distribution, ProfitPeriod, ProfitStatus

SPLIT = (("ENTERPRISE", Decimal("60.00")), ("SHAREHOLDERS", Decimal("20.00")), ("INVESTORS", Decimal("20.00")))


def _months_inclusive(start, end):
    return (end.year - start.year) * 12 + end.month - start.month + 1


def _salary_for_period(start, end):
    total = Decimal("0.00")
    inputs = []
    agents = User.objects.filter(role=Role.AGENT)
    for agent in agents:
        employment_start = agent.agent_started_on or agent.date_joined.date()
        if employment_start > end or (agent.agent_ended_on and agent.agent_ended_on < start):
            continue
        effective_start = max(start, employment_start)
        effective_end = min(end, agent.agent_ended_on or end)
        months = _months_inclusive(effective_start, effective_end)
        amount = agent.monthly_salary_usd * months
        total += amount
        inputs.append({"agent_id": agent.pk, "start": str(effective_start), "end": str(effective_end), "months": months, "monthly_usd": str(agent.monthly_salary_usd), "total_usd": str(amount)})
    return total, inputs

@transaction.atomic
def calculate_profit_period(*, period_id, actor):
    if actor.role != Role.ADMIN:
        raise PermissionDenied("Seul un administrateur peut finaliser une période de bénéfice.")
    period = ProfitPeriod.objects.select_for_update().get(pk=period_id)
    if period.status == ProfitStatus.FINALIZED:
        raise ValidationError("Cette période est déjà finalisée.")
    period_start, period_end = business_day_bounds(period.start_date, period.end_date)
    operations = Operation.objects.filter(
        status=OperationStatus.COMPLETED,
        currency=period.currency,
        created_at__gte=period_start,
        created_at__lt=period_end,
    )
    gross = operations.aggregate(v=Sum("fee"))["v"] or Decimal("0.00")
    expenses = Expense.objects.filter(status=ExpenseStatus.APPROVED, currency=period.currency, incurred_on__range=(period.start_date, period.end_date))
    validated_expenses = expenses.aggregate(v=Sum("amount"))["v"] or Decimal("0.00")
    salary_expenses = expenses.filter(category="SALARY")
    salary_expense = salary_expenses.aggregate(v=Sum("amount"))["v"] or Decimal("0.00")
    salary_inputs = list(salary_expenses.values("agent_id", "amount", "incurred_on"))
    salary_usd = (salary_expense * period.currency.current_rate().rate_to_usd).quantize(Decimal("0.01")) if period.currency.current_rate() else Decimal("0.00")
    deductible = validated_expenses
    net = gross - deductible
    period.gross_fees, period.deductible_expenses, period.net_profit = gross, deductible, net
    period.snapshot = {
        "operation_ids": list(operations.values_list("id", flat=True)),
        "expense_ids": list(expenses.values_list("id", flat=True)),
        "validated_expenses": str(validated_expenses), "agent_salary_usd": str(salary_usd),
        "salary_inputs": salary_inputs,
    }
    period.status, period.calculated_by, period.calculated_at = ProfitStatus.FINALIZED, actor, timezone.now()
    period.save()
    Allocation.objects.filter(period=period).delete()
    allocated = Decimal("0.00")
    for index, (bucket, percentage) in enumerate(SPLIT):
        amount = net - allocated if index == len(SPLIT) - 1 else (net * percentage / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        Allocation.objects.create(period=period, bucket=bucket, percentage=percentage, amount=amount)
        allocated += amount
    if allocated != net:
        raise ValidationError("La répartition ne correspond pas au bénéfice net.")
    record(actor=actor, action="PROFIT_FINALIZE", instance=period, after={"net": str(net)})
    return period


@transaction.atomic
def allocate_individual_distributions(*, period_id, actor):
    if actor.role != Role.ADMIN:
        raise PermissionDenied("Seul un administrateur peut allouer les distributions.")
    period = ProfitPeriod.objects.select_for_update().get(pk=period_id, status=ProfitStatus.FINALIZED)
    type_by_bucket = {"SHAREHOLDERS": StakeholderType.SHAREHOLDER, "INVESTORS": StakeholderType.INVESTOR}
    for allocation in period.allocations.filter(bucket__in=type_by_bucket):
        if allocation.amount <= 0:
            continue
        if allocation.distributions.exists():
            raise ValidationError("Les distributions de cette enveloppe sont déjà figées.")
        investments = Investment.objects.filter(stakeholder__type=type_by_bucket[allocation.bucket], currency=period.currency, stakeholder__is_active=True, invested_on__lte=period.end_date)
        total = investments.aggregate(v=Sum("amount"))["v"] or Decimal("0.00")
        if total <= 0:
            continue
        by_stakeholder = investments.values("stakeholder_id").annotate(invested=Sum("amount")).order_by("stakeholder_id")
        distributed = Decimal("0.00")
        rows = list(by_stakeholder)
        for index, row in enumerate(rows):
            amount = allocation.amount - distributed if index == len(rows) - 1 else (allocation.amount * row["invested"] / total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            Distribution.objects.create(allocation=allocation, stakeholder_id=row["stakeholder_id"], amount=amount)
            distributed += amount
        period.snapshot.setdefault("distribution_inputs", {})[allocation.bucket] = {
            "investment_total": str(total),
            "stakeholders": [{"stakeholder_id": row["stakeholder_id"], "invested": str(row["invested"])} for row in rows],
        }
    period.save(update_fields=["snapshot"])
    record(actor=actor, action="DISTRIBUTIONS_ALLOCATE", instance=period)
    return period
