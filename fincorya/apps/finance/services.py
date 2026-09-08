from collections import defaultdict
import hashlib
import json
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError


from django.db.models import Sum
from django.utils import timezone

from apps.accounts.permissions import require_finance_access
from apps.audit.services import record as audit_record
from .locking import ledger_atomic, save_internal, monthly_close

from .models import (
    AccountNature,
    AccountType,
    BatchStatus,
    EntrySide,
    FinancialAccount,
    FinancialPeriod,
    JournalBatch,
    LedgerEntry,
    MigrationStatus,
    PeriodStatus,
)


def _natural_delta(account, side, amount):
    debit_positive = account.nature in {AccountNature.ASSET, AccountNature.EXPENSE}
    return amount if (side == EntrySide.DEBIT) == debit_positive else -amount


def _assert_open_period(effective_at, *, monthly_accrual=False):
    business_day = effective_at.astimezone(ZoneInfo(settings.BUSINESS_TIME_ZONE)).date()
    locked = FinancialPeriod.objects.filter(
        status=PeriodStatus.LOCKED, start_date__lte=business_day, end_date__gte=business_day
    )
    period = monthly_close.get()
    if monthly_accrual and period and period.start_date <= business_day <= period.end_date:
        # Only the monthly non-cash accrual may follow a cash-day/week closure.
        # A locked month still forbids every posting, including accruals.
        locked = locked.filter(period_type="MONTH")
    if locked.exists():
        raise ValidationError("La période financière correspondante est verrouillée.")


@ledger_atomic
def prepare_batch(*, actor, idempotency_key, event_type, effective_at, description, lines, source_model="", source_id=""):
    require_finance_access(actor, "prepare")
    return _prepare_batch(actor=actor, idempotency_key=idempotency_key, event_type=event_type, effective_at=effective_at, description=description, lines=lines, source_model=source_model, source_id=source_id)


@ledger_atomic
def _prepare_batch(*, actor, idempotency_key, event_type, effective_at, description, lines, source_model="", source_id=""):
    if not idempotency_key or len(idempotency_key) > 120 or timezone.is_naive(effective_at):
        raise ValidationError("Clé d’idempotence et date avec fuseau obligatoires.")
    lines = list(lines)
    payload = {"event": event_type, "date": effective_at.isoformat(), "description": description, "source": [source_model, str(source_id)], "lines": [
        {"account": row["account"].pk, "currency": row["currency"].pk, "side": row["side"], "amount": str(Decimal(row["amount"]).quantize(Decimal("0.01"))), "memo": row.get("memo", "")} for row in lines
    ]}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    existing = JournalBatch.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        if existing.payload_hash != digest:
            raise ValidationError("Cette clé d’idempotence désigne un autre contenu.")
        return existing
    if source_model and source_id and JournalBatch.objects.filter(source_model=source_model, source_id=str(source_id), event_type=event_type).exists():
        raise ValidationError("Cet événement possède déjà un lot ; réutilisez sa clé.")
    batch = JournalBatch.objects.create(
        idempotency_key=idempotency_key,
        event_type=event_type,
        effective_at=effective_at,
        description=description,
        source_model=source_model,
        source_id=str(source_id) if source_id else "",
        created_by=actor,
        payload_hash=digest,
    )
    for line in lines:
        LedgerEntry.objects.create(batch=batch, **line)
    audit_record(actor=actor, action="JOURNAL_PREPARE", instance=batch, after={"lines": len(lines)})
    return batch


@ledger_atomic
def post_batch(*, batch_id, actor):
    require_finance_access(actor, "approve")
    return _post_batch(batch_id=batch_id, actor=actor)


@ledger_atomic
def _post_batch(*, batch_id, actor):
    batch = JournalBatch.objects.select_for_update().get(pk=batch_id)
    if batch.status == BatchStatus.POSTED:
        return batch
    if batch.status != BatchStatus.DRAFT:
        raise ValidationError("Seul un lot brouillon peut être publié.")
    entries = list(batch.entries.select_related("account", "currency").order_by("account_id", "id"))
    monthly_accrual = (
        batch.event_type == "EXPENSE_RECOGNITION" and batch.source_model == "expenses.Expense"
        and entries and all(e.account.account_type in {AccountType.EXPENSE, AccountType.EXPENSE_PAYABLE} for e in entries)
    )
    _assert_open_period(batch.effective_at, monthly_accrual=monthly_accrual)
    if len(entries) < 2:
        raise ValidationError("Un lot doit contenir au moins deux écritures.")
    totals = defaultdict(lambda: {EntrySide.DEBIT: Decimal("0"), EntrySide.CREDIT: Decimal("0")})
    for entry in entries:
        entry.full_clean()
        if not entry.account.is_active:
            raise ValidationError("Un compte inactif ne peut pas être mouvementé.")
        if entry.account.cutover_at and batch.effective_at < entry.account.cutover_at:
            raise ValidationError("Écriture antérieure à la bascule interdite.")
        totals[entry.currency_id][entry.side] += entry.amount
    if any(sides[EntrySide.DEBIT] != sides[EntrySide.CREDIT] for sides in totals.values()):
        raise ValidationError("Le lot n’est pas équilibré par devise.")
    accounts = {row.pk: row for row in FinancialAccount.objects.select_for_update().filter(pk__in={e.account_id for e in entries}).order_by("pk")}
    for entry in entries:
        account = accounts[entry.account_id]
        account.cached_balance += _natural_delta(account, entry.side, entry.amount)
    for account in accounts.values():
        save_internal(account, update_fields=["cached_balance"])
    batch.status = BatchStatus.POSTED
    batch.posted_by = actor
    batch.posted_at = timezone.now()
    save_internal(batch, update_fields=["status", "posted_by", "posted_at"])
    audit_record(actor=actor, action="JOURNAL_POST", instance=batch, after={"currencies": len(totals)})
    return batch


@ledger_atomic
def reverse_batch(*, batch_id, actor, reason, idempotency_key):
    require_finance_access(actor, "approve")
    original = JournalBatch.objects.select_for_update().get(pk=batch_id)
    if original.status == BatchStatus.REVERSED:
        existing = JournalBatch.objects.filter(reversal_of=original, idempotency_key=idempotency_key).first()
        if existing:
            return existing
    if original.status != BatchStatus.POSTED:
        raise ValidationError("Seul un lot publié peut être contre-passé.")
    if not reason.strip():
        raise ValidationError("Le motif de contre-passation est obligatoire.")
    lines = [
        {"account": e.account, "side": EntrySide.CREDIT if e.side == EntrySide.DEBIT else EntrySide.DEBIT,
         "amount": e.amount, "currency": e.currency, "memo": reason.strip()}
        for e in original.entries.select_related("account", "currency")
    ]
    reversal = prepare_batch(
        actor=actor, idempotency_key=idempotency_key, event_type="REVERSAL", effective_at=timezone.now(),
        description=f"Contre-passation: {original.description}", lines=lines,
        source_model="finance.JournalBatch", source_id=original.pk,
    )
    if reversal.reversal_of_id is None:
        reversal.reversal_of = original
        reversal.save(update_fields=["reversal_of"])
    post_batch(batch_id=reversal.pk, actor=actor)
    original.status = BatchStatus.REVERSED
    save_internal(original, update_fields=["status"])
    return reversal


def ledger_balance(account, *, before=None):
    entries = account.entries.filter(batch__status__in=[BatchStatus.POSTED, BatchStatus.REVERSED])
    if before:
        entries = entries.filter(batch__effective_at__lt=before)
    debit = entries.filter(side=EntrySide.DEBIT).aggregate(v=Sum("amount"))["v"] or Decimal("0")
    credit = entries.filter(side=EntrySide.CREDIT).aggregate(v=Sum("amount"))["v"] or Decimal("0")
    return debit - credit if account.nature in {AccountNature.ASSET, AccountNature.EXPENSE} else credit - debit


def reconcile_account(account):
    reference = ledger_balance(account)
    return {"cached": account.cached_balance, "ledger": reference, "difference": account.cached_balance - reference}




def migrate_legacy_opening_balances(**kwargs):
    from .cutover import migrate_opening_balances
    return migrate_opening_balances(**kwargs)
