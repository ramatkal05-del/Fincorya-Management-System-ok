"""Adapters for existing domain objects; every event owns exactly one batch."""
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.accounts.permissions import require_finance_access
from .cutover import active_cutover
from .locking import ledger_atomic
from .models import AccountNature, AccountType, EconomicRule, FinancialAccount, JournalBatch
from .services import _prepare_batch, _post_batch, ledger_balance


def effective_rule(party, kind, on_date):
    rule = EconomicRule.objects.filter(stakeholder=party, kind=kind, effective_from__lte=on_date).order_by("-effective_from").first()
    if rule and rule.effective_to and rule.effective_to < on_date:
        return None
    return rule


def counterpart(currency, account_type, *, party=None):
    nature = {
        AccountType.CAPITAL: AccountNature.EQUITY, AccountType.COMMISSION: AccountNature.INCOME,
        AccountType.EXPENSE: AccountNature.EXPENSE, AccountType.PARTNER_PAYABLE: AccountNature.LIABILITY,
        AccountType.EXPENSE_PAYABLE: AccountNature.LIABILITY, AccountType.DISTRIBUTION_PAYABLE: AccountNature.LIABILITY,
        AccountType.TRANSIT: AccountNature.LIABILITY, AccountType.PARTNER_ADVANCE: AccountNature.LIABILITY,
        AccountType.ADJUSTMENT: AccountNature.EQUITY,
    }[account_type]
    code = f"{account_type}-{currency.code}" + (f"-{party.pk}" if party else "")
    account, _ = FinancialAccount.objects.get_or_create(code=code, defaults={
        "name": f"{AccountType(account_type).label} {party.name if party else ''}".strip(),
        "account_type": account_type, "nature": nature, "currency": currency, "economic_owner": party})
    if account.currency_id != currency.pk or account.nature != nature or account.economic_owner_id != (party.pk if party else None):
        raise ValidationError("Compte de contrepartie incompatible.")
    return account


def line(account, side, amount):
    return {"account": account, "currency": account.currency, "side": side, "amount": amount}


@ledger_atomic
def record_event(*, actor, source, event, effective_at, lines, description=None):
    source_name = source._meta.label
    key = f"{source_name}-{source.pk}-{event}"
    batch = _prepare_batch(actor=actor, idempotency_key=key, event_type=event, effective_at=effective_at,
        description=description or f"{event} #{source.pk}", lines=[row for row in lines if row["amount"]],
        source_model=source_name, source_id=source.pk)
    return _post_batch(batch_id=batch.pk, actor=actor)


def mapped_cash(legacy):
    field = "legacy_global_account" if legacy._meta.model_name == "globalcashaccount" else "legacy_cash_account"
    try:
        return FinancialAccount.objects.get(**{field: legacy})
    except FinancialAccount.DoesNotExist as exc:
        raise ValidationError("Cette caisse n’a pas de reprise réconciliée.") from exc


def _assert_projection(account, legacy):
    if ledger_balance(account) != legacy.balance:
        raise ValidationError("Le cache legacy diverge du grand livre ; transaction annulée.")


@ledger_atomic
def record_operation(operation, actor):
    run = active_cutover()
    if not run:
        return None
    if operation.created_at < run.cutover_at:
        raise ValidationError("Opération antérieure à la bascule : traitement de transition requis.")
    account = mapped_cash(operation.account)
    transit = counterpart(operation.currency, AccountType.TRANSIT)
    income = counterpart(operation.currency, AccountType.COMMISSION)
    partner_fee = Decimal("0")
    attribution = getattr(operation, "partner_attribution", None)
    if attribution:
        from config.business_time import business_date
        rule = effective_rule(attribution.stakeholder, "COMMISSION", business_date(operation.created_at))
        if not rule:
            raise ValidationError("La commission partenaire exige une règle à date d’effet confirmée.")
        partner_fee = (operation.fee * rule.value / 100).quantize(Decimal("0.01"))
        attribution.share_percent = rule.value
        attribution.save(update_fields=["share_percent"])
    if operation.type == "SENT_TRANSFER":
        lines = [line(account, "DEBIT", operation.amount + operation.fee), line(transit, "CREDIT", operation.amount)]
    else:
        lines = [line(transit, "DEBIT", operation.amount), line(account, "CREDIT", operation.amount - operation.fee)]
    lines.append(line(income, "CREDIT", operation.fee - partner_fee))
    if partner_fee:
        lines.append(line(counterpart(operation.currency, AccountType.PARTNER_PAYABLE, party=attribution.stakeholder), "CREDIT", partner_fee))
    result = record_event(actor=actor, source=operation, event="OPERATION", effective_at=operation.created_at, lines=lines)
    operation.account.refresh_from_db()
    _assert_projection(account, operation.account)
    return result


@ledger_atomic
def record_funding(funding, actor):
    if not active_cutover():
        return
    target = mapped_cash(funding.account)
    source = mapped_cash(funding.account.global_account)
    result = record_event(actor=actor, source=funding, event="CASH_TRANSFER", effective_at=funding.created_at,
                        lines=[line(target, "DEBIT", funding.amount), line(source, "CREDIT", funding.amount)])
    funding.account.refresh_from_db()
    funding.account.global_account.refresh_from_db()
    _assert_projection(target, funding.account)
    _assert_projection(source, funding.account.global_account)
    return result


@ledger_atomic
def record_capital(movement, actor):
    if not active_cutover():
        return
    cash = mapped_cash(movement.global_account)
    capital = counterpart(cash.currency, AccountType.CAPITAL)
    cash_side = "DEBIT" if movement.direction == "IN" else "CREDIT"
    result = record_event(actor=actor, source=movement, event="CAPITAL", effective_at=movement.created_at,
        lines=[line(cash, cash_side, movement.amount), line(capital, "CREDIT" if cash_side == "DEBIT" else "DEBIT", movement.amount)])
    _assert_projection(cash, movement.global_account)
    return result


@ledger_atomic
def record_handover(handover, actor):
    if not active_cutover():
        return
    source = mapped_cash(handover.account)
    target = mapped_cash(handover.account.global_account)
    return record_event(actor=actor, source=handover, event="CASH_TRANSFER", effective_at=handover.resolved_at,
                        lines=[line(target, "DEBIT", handover.amount), line(source, "CREDIT", handover.amount)])


@ledger_atomic
def recognize_expense(expense, actor):
    if not active_cutover():
        return
    if expense.recognition_batch_id:
        return expense.recognition_batch
    from config.business_time import business_day_bounds
    when, _ = business_day_bounds(expense.incurred_on, expense.incurred_on)
    if when < active_cutover().cutover_at:
        raise ValidationError("Charge antérieure à la bascule : reprise spécifique requise.")
    batch = record_event(actor=actor, source=expense, event="EXPENSE_RECOGNITION", effective_at=when,
        lines=[line(counterpart(expense.currency, AccountType.EXPENSE), "DEBIT", expense.amount),
               line(counterpart(expense.currency, AccountType.EXPENSE_PAYABLE, party=expense.stakeholder), "CREDIT", expense.amount)])
    expense.recognition_batch = batch
    expense.save(update_fields=["recognition_batch"])
    return batch


@ledger_atomic
def pay_expense(*, expense_id, account_id, amount, actor, idempotency_key):
    require_finance_access(actor, "approve")
    if not active_cutover():
        raise ValidationError("Le moteur financier est désactivé.")
    from apps.expenses.models import Expense, ExpensePayment
    expense = Expense.objects.select_for_update().get(pk=expense_id)
    cash = FinancialAccount.objects.get(pk=account_id)
    existing = JournalBatch.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        payment = ExpensePayment.objects.filter(batch=existing, expense=expense, amount=amount).first()
        if payment and existing.entries.filter(account=cash, side="CREDIT", amount=amount).exists():
            return payment
        raise ValidationError("Clé de paiement déjà utilisée pour un autre contenu.")
    if expense.status != "APPROVED" or not expense.recognition_batch_id:
        raise ValidationError("La charge doit être reconnue et approuvée avant paiement.")
    from django.db.models import Sum
    paid = expense.payments.aggregate(v=Sum("amount"))["v"] or Decimal("0")
    if amount <= 0 or paid + amount > expense.amount:
        raise ValidationError("Paiement supérieur au reste dû ou non positif.")
    _validate_cash_payment(cash, expense.currency_id, amount)
    payable = counterpart(expense.currency, AccountType.EXPENSE_PAYABLE, party=expense.stakeholder)
    batch = _prepare_batch(actor=actor, idempotency_key=idempotency_key, event_type="EXPENSE_PAYMENT",
        effective_at=timezone.now(), description=f"Paiement charge #{expense.pk}", lines=[line(payable, "DEBIT", amount), line(cash, "CREDIT", amount)])
    _post_batch(batch_id=batch.pk, actor=actor)
    _project_payment(cash, amount, actor, f"Paiement charge #{expense.pk}")
    return ExpensePayment.objects.create(expense=expense, batch=batch, amount=amount, paid_at=batch.effective_at)


def _validate_cash_payment(cash, currency_id, amount):
    if not cash.is_active:
        raise ValidationError("Le compte de paiement est inactif.")
    if cash.nature != AccountNature.ASSET or cash.account_type not in {"AGENT_CASH", "GLOBAL_CASH", "MOBILE_MONEY", "DIGITAL", "FINANCIAL_SERVICE"}:
        raise ValidationError("Sélectionnez un compte de trésorerie.")
    if cash.currency_id != currency_id or ledger_balance(cash) < amount:
        raise ValidationError("Devise incompatible ou trésorerie insuffisante.")


@ledger_atomic
def pay_partner(*, party_id, account_id, amount, actor, idempotency_key):
    require_finance_access(actor, "approve")
    if not active_cutover():
        raise ValidationError("Le moteur financier est désactivé.")
    from apps.stakeholders.models import Stakeholder
    party = Stakeholder.objects.get(pk=party_id)
    cash = FinancialAccount.objects.get(pk=account_id)
    payable = counterpart(cash.currency, AccountType.PARTNER_PAYABLE, party=party)
    existing = JournalBatch.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        if (existing.event_type == "PARTNER_PAYMENT" and existing.status == "POSTED"
                and existing.entries.filter(account=cash, side="CREDIT", amount=amount).exists()
                and existing.entries.filter(account=payable, side="DEBIT", amount=amount).exists()):
            return existing
        raise ValidationError("Cette clé désigne un autre règlement.")
    if amount <= 0 or amount > ledger_balance(payable):
        raise ValidationError("Le règlement dépasse les commissions dues ou est non positif.")
    _validate_cash_payment(cash, payable.currency_id, amount)
    batch = _prepare_batch(actor=actor, idempotency_key=idempotency_key, event_type="PARTNER_PAYMENT",
        effective_at=timezone.now(), description=f"Règlement commissions partenaire #{party.pk}",
        lines=[line(payable, "DEBIT", amount), line(cash, "CREDIT", amount)])
    _post_batch(batch_id=batch.pk, actor=actor)
    _project_payment(cash, amount, actor, f"Règlement partenaire #{party.pk}")
    return batch


def _project_payment(cash, amount, actor, note):
    # Compatibility projection, never an independent financial event.
    if cash.legacy_cash_account_id:
        from apps.cash.services import apply_movement
        legacy = cash.legacy_cash_account
        legacy.refresh_from_db()
        apply_movement(account=legacy, direction="OUT", amount=amount, movement_type="ADJUSTMENT", actor=actor, note=note)
        _assert_projection(cash, legacy)
    elif cash.legacy_global_account_id:
        from apps.cash.services import _apply_global_movement
        legacy = cash.legacy_global_account
        legacy.refresh_from_db()
        _apply_global_movement(global_account=legacy, direction="OUT", amount=amount, movement_type="CAPITAL_OUT", actor=actor, note=note)
        _assert_projection(cash, legacy)
