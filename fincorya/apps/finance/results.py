"""Monthly result and shareholder distribution plan, computed from the ledger only.

Result = own commissions + FINCORYA share of partner commissions
         − salaries − other expenses − supplier fees − investor remunerations
         − losses carried forward.
A loss produces no distribution and is carried to the next month.
"""
from decimal import Decimal, ROUND_DOWN

from django.core.exceptions import ValidationError
from django.db.models import Sum

from config.business_time import business_day_bounds
from .models import AccountNature, AccountType, DistributionMode, DistributionPolicy, FinancialAccount, RuleKind
from .services import ledger_balance

ZERO = Decimal("0.00")
MISSING_POLICY_MESSAGE = ("Aucune politique de distribution datée n’est en vigueur. Configurez-la dans Finance › Distribution › "
                          "« Nouvelle politique datée » : mode, actionnaires concernés, pourcentages (total 100 %) et date d’entrée en vigueur.")
EXPENSE_GROUPS = {
    "salaries": {"SALARY", "OTHER"},          # OTHER carries the dated staff remunerations accrued at closing
    "supplier_fees": {"SUPPLIER_FEE"},
    "investor_returns": {"INVESTOR_RETURN"},
}


def _delta(account, start, end):
    return ledger_balance(account, before=end) - ledger_balance(account, before=start)


def _expense_group(account):
    category = account.code.split("-", 2)[2] if account.code.count("-") >= 2 else ""
    for group, categories in EXPENSE_GROUPS.items():
        if category in categories:
            return group
    return "other_expenses"


def policy_for(on_date):
    return DistributionPolicy.objects.filter(effective_from__lte=on_date).order_by("-effective_from").first()


def prior_losses(currency, start_date):
    from apps.profits.models import ProfitPeriod
    previous = ProfitPeriod.objects.filter(currency=currency, status="FINALIZED", end_date__lt=start_date, finance_period__isnull=False).order_by("-end_date").first()
    if previous is None or previous.distributable >= 0:
        return ZERO
    return -previous.distributable


def compute_result(period, currency):
    """Return the result breakdown of a closed or closing month for one currency."""
    from apps.stakeholders.models import PartnerOperation
    start, end = business_day_bounds(period.start_date, period.end_date)
    accounts = FinancialAccount.objects.filter(currency=currency).select_related("currency")
    income = sum((_delta(a, start, end) for a in accounts.filter(nature=AccountNature.INCOME)), ZERO)
    expenses = {"salaries": ZERO, "other_expenses": ZERO, "supplier_fees": ZERO, "investor_returns": ZERO}
    for account in accounts.filter(nature=AccountNature.EXPENSE):
        expenses[_expense_group(account)] += _delta(account, start, end)
    attributed = PartnerOperation.objects.filter(operation__status="COMPLETED", operation__currency=currency,
                                                 operation__created_at__gte=start, operation__created_at__lt=end).select_related("operation")
    partner_gross = sum((row.operation.fee for row in attributed), ZERO)
    partner_share = sum(((row.operation.fee * row.share_percent / 100).quantize(Decimal("0.01")) for row in attributed), ZERO)
    fincorya_share = partner_gross - partner_share
    own = income - fincorya_share
    total_expenses = sum(expenses.values(), ZERO)
    net = income - total_expenses
    losses = prior_losses(currency, period.start_date)
    distributable = net - losses
    # FX effects are reported apart and excluded from the distributable amount until their treatment is decided.
    fx = {"fx_realized": ZERO, "fx_revaluation": ZERO}
    for account in accounts.filter(account_type=AccountType.FX_DIFFERENCE):
        if account.code.endswith("-REALIZED"):
            fx["fx_realized"] += _delta(account, start, end)
        elif account.code.endswith("-REVALUATION"):
            fx["fx_revaluation"] += _delta(account, start, end)
    return {
        "currency": currency.code, "own_commissions": own, "partner_gross": partner_gross, "partner_share": partner_share,
        "fincorya_share": fincorya_share, "income": income, **expenses, "total_expenses": total_expenses,
        "net_result": net, "prior_losses": losses, "distributable": distributable, **fx,
        "fx_note": "Écarts de change exclus de la distribution ; traitement à définir par l’administration.",
        "cash": sum((ledger_balance(a, before=end) for a in accounts.filter(account_type__in=["AGENT_CASH", "GLOBAL_CASH", "MOBILE_MONEY", "DIGITAL", "FINANCIAL_SERVICE"])), ZERO),
    }


def distribution_plan(period, *, on_date=None):
    """Weights per shareholder frozen at closing, according to the dated policy in force."""
    from apps.stakeholders.models import Stakeholder
    from .events import effective_rule
    on_date = on_date or period.end_date
    policy = policy_for(on_date)
    if policy is None:
        raise ValidationError(MISSING_POLICY_MESSAGE)
    if policy.effective_from > period.start_date:
        raise ValidationError("La politique de distribution change en cours de mois ; période à qualifier.")
    shares = list(policy.shares.select_related("stakeholder").order_by("stakeholder_id"))
    if policy.mode in {DistributionMode.EQUAL_SHARES, DistributionMode.CAPITAL_PROPORTIONAL}:
        if shares:
            selected = [share.stakeholder_id for share in shares]
        elif policy.mode == DistributionMode.EQUAL_SHARES:
            # No explicit selection: equal shares among every active shareholder.
            selected = list(Stakeholder.objects.filter(type="SHAREHOLDER", is_active=True).order_by("pk").values_list("pk", flat=True))
        else:
            selected = list(Stakeholder.objects.filter(type="SHAREHOLDER", is_active=True).order_by("pk").values_list("pk", flat=True))
        if not selected:
            raise ValidationError("Aucun actionnaire actif à rémunérer ; sélectionnez-les dans la politique de distribution.")
        if policy.mode == DistributionMode.EQUAL_SHARES:
            weights = [{"stakeholder_id": party_id, "weight": "1"} for party_id in selected]
        else:
            from .models import FinancialAccount as Account
            weights = []
            for party_id in selected:
                capital = sum((ledger_balance(a) for a in Account.objects.filter(economic_owner_id=party_id, account_type=AccountType.CAPITAL)), ZERO)
                if capital > 0:
                    weights.append({"stakeholder_id": party_id, "weight": str(capital)})
            if not weights:
                raise ValidationError("Aucun apport en capital enregistré : la répartition proportionnelle est impossible.")
    else:
        weights = []
        for party in Stakeholder.objects.filter(financial_rules__kind=RuleKind.DIVIDEND).distinct().order_by("pk"):
            rule = effective_rule(party, RuleKind.DIVIDEND, on_date)
            if rule:
                if rule.effective_from > period.start_date:
                    raise ValidationError("Une règle de distribution change en cours de mois ; période à qualifier.")
                weights.append({"stakeholder_id": party.pk, "weight": str(rule.value), "rule_id": rule.pk})
        if weights and sum((Decimal(row["weight"]) for row in weights), ZERO) != 100:
            raise ValidationError("Les règles datées de dividende ne totalisent pas 100 %.")
    return {"policy_id": policy.pk, "mode": policy.mode, "effective_from": str(policy.effective_from), "weights": weights}


def split_amount(amount, weights):
    """Split `amount` by weights to the cent; the last part absorbs rounding so parts always sum to the amount."""
    amount = Decimal(amount).quantize(Decimal("0.01"))
    total = sum((Decimal(row["weight"]) for row in weights), ZERO)
    if amount <= 0 or total <= 0:
        raise ValidationError("Montant ou pondérations invalides.")
    parts, remaining = [], amount
    for index, row in enumerate(weights):
        part = remaining if index == len(weights) - 1 else (amount * Decimal(row["weight"]) / total).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        parts.append((row["stakeholder_id"], part))
        remaining -= part
    assert sum((part for _, part in parts), ZERO) == amount
    return parts
