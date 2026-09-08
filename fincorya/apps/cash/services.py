"""
Cash services.

`apply_movement` is the single choke point that ever changes
`CashAccount.balance`. All callers use a lock and create a paired ledger row.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError


from apps.finance.locking import ledger_atomic
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.audit.services import record as audit_record
from apps.accounts.models import Role, User
from apps.notifications.services import notify

from .models import (
    CashAccount, CashHandover, CashMovement, ClosureStatus, GlobalCashAccount, GlobalCashMovement,
    DailyClosure, HandoverStatus, MovementDirection, MovementType,
    CashFunding, GlobalMovementType,
)


def _apply_global_movement(*, global_account, direction, amount, movement_type, actor,
                           note, funding=None, handover=None):
    """Change a locked global reserve and append its inseparable ledger line."""
    if amount <= 0:
        raise ValidationError(_("Le montant du mouvement global doit être positif."))
    before = global_account.balance
    if direction == MovementDirection.IN:
        global_account.balance += amount
    else:
        if global_account.balance < amount:
            raise ValidationError(_("Capital insuffisant dans la caisse globale."))
        global_account.balance -= amount
    global_account._ledger_write = True
    try:
        global_account.save(update_fields=["balance"])
    finally:
        del global_account._ledger_write
    movement = GlobalCashMovement.objects.create(
        global_account=global_account, direction=direction, movement_type=movement_type,
        amount=amount, balance_after=global_account.balance, funding=funding,
        handover=handover, note=note.strip(), created_by=actor,
    )
    audit_record(
        actor=actor, action="GLOBAL_CASH_MOVEMENT", instance=movement,
        before={"reserve": str(before)},
        after={"reserve": str(global_account.balance), "amount": str(amount), "direction": direction},
    )
    return movement


@ledger_atomic
def adjust_global_cash(*, global_account_id, direction, amount, adjusted_by, note):
    if adjusted_by.role != Role.ADMIN:
        raise PermissionDenied(_("Seul l'administrateur peut modifier le capital global."))
    if direction not in {MovementDirection.IN, MovementDirection.OUT}:
        raise ValidationError(_("Direction de mouvement invalide."))
    if not note.strip():
        raise ValidationError(_("Une justification est obligatoire."))
    global_account = GlobalCashAccount.objects.select_for_update().get(pk=global_account_id)
    movement_type = GlobalMovementType.CAPITAL_IN if direction == MovementDirection.IN else GlobalMovementType.CAPITAL_OUT
    movement = _apply_global_movement(
        global_account=global_account, direction=direction, amount=amount,
        movement_type=movement_type, actor=adjusted_by, note=note,
    )
    from apps.finance.events import record_capital
    record_capital(movement, adjusted_by)
    return movement


@ledger_atomic
def allocate_cash(*, account_id: int, amount: Decimal, allocated_by, note: str = "") -> CashFunding:
    if allocated_by.role != Role.ADMIN:
        raise PermissionDenied(_("Seul l'administrateur peut allouer une caisse."))
    if amount <= 0:
        raise ValidationError(_("Le montant alloué doit être supérieur à zéro."))
    account = CashAccount.objects.select_for_update().get(pk=account_id)
    if not account.global_account_id:
        raise ValidationError(_("Cette caisse agent doit être liée à une caisse globale."))
    global_account = GlobalCashAccount.objects.select_for_update().get(pk=account.global_account_id)
    if global_account.currency_id != account.currency_id:
        raise ValidationError(_("La devise de la caisse globale ne correspond pas à celle de la caisse agent."))
    if not global_account.is_active:
        raise ValidationError(_("La caisse globale est inactive."))
    if global_account.balance < amount:
        raise ValidationError(_("Capital insuffisant dans la caisse globale."))
    account.is_active = True
    account.allocated_by = allocated_by
    account.allocated_at = timezone.now()
    account.save(update_fields=["is_active", "allocated_by", "allocated_at"])
    funding = CashFunding.objects.create(account=account, amount=amount, note=note.strip(), allocated_by=allocated_by)
    _apply_global_movement(
        global_account=global_account, direction=MovementDirection.OUT, amount=amount,
        movement_type=GlobalMovementType.ALLOCATION, actor=allocated_by,
        note=f"Allocation vers la caisse agent #{account.pk}: {note.strip()}".rstrip(), funding=funding,
    )
    apply_movement(account=account, direction=MovementDirection.IN, amount=amount, movement_type=MovementType.OPENING,
                   actor=allocated_by, note=f"Allocation administrateur: {note.strip()}".rstrip())
    funding.applied_at = timezone.now()
    funding.save(update_fields=["applied_at"])
    audit_record(actor=allocated_by, action="CASH_FUND", instance=funding, after={"amount": str(amount), "account": account_id, "global_account": global_account.pk, "global_balance": str(global_account.balance)})
    from apps.finance.events import record_funding
    record_funding(funding, allocated_by)
    return funding


def _can_manage_account(actor, account, *, close=False):
    if actor.role == Role.ADMIN:
        return True
    if actor.role == Role.AGENT:
        return account.agent_id == actor.pk
    return False


def apply_movement(*, account: CashAccount, direction: str, amount: Decimal,
                    movement_type: str, actor, operation=None, note: str = "") -> CashMovement:
    """Must be called from inside a transaction.atomic() block by a locked account."""
    if amount <= 0:
        raise ValidationError(_("Le montant du mouvement doit être positif."))
    if account.is_locked:
        raise ValidationError(_("Ce compte est verrouillé (clôture en cours)."))

    if direction == MovementDirection.IN:
        account.balance += amount
    else:
        if account.balance < amount:
            raise ValidationError(_("Fonds insuffisants dans la caisse."))
        account.balance -= amount
    account._ledger_write = True
    try:
        account.save(update_fields=["balance"])
    finally:
        del account._ledger_write

    return CashMovement.objects.create(
        account=account, direction=direction, movement_type=movement_type,
        amount=amount, balance_after=account.balance, operation=operation,
        note=note, created_by=actor,
    )


@ledger_atomic
def handover_cash(*, account_id: int, amount: Decimal, requested_by) -> CashHandover:
    account = CashAccount.objects.select_for_update().get(pk=account_id)
    if not _can_manage_account(requested_by, account):
        raise PermissionDenied(_("Vous n'êtes pas autorisé à créer une remise pour cette caisse."))
    if amount <= 0:
        raise ValidationError(_("Le montant de la remise doit être positif."))
    if account.balance < amount:
        raise ValidationError(_("Le montant de la remise dépasse le solde de la caisse."))
    handover = CashHandover.objects.create(account=account, amount=amount, requested_by=requested_by)
    audit_record(actor=requested_by, action="HANDOVER_REQUEST", instance=handover, after={"amount": str(amount)})
    return handover


@ledger_atomic
def confirm_handover(*, handover_id: int, confirmed_by) -> CashHandover:
    handover = CashHandover.objects.select_for_update().get(pk=handover_id)
    if handover.status != HandoverStatus.PENDING:
        raise ValidationError(_("Cette remise a déjà été traitée."))
    account = CashAccount.objects.select_for_update().get(pk=handover.account_id)
    if confirmed_by.role != Role.ADMIN:
        raise PermissionDenied(_("Seul un administrateur peut confirmer cette remise."))
    if handover.requested_by_id == confirmed_by.pk:
        raise ValidationError(_("La remise doit être confirmée par une autre personne."))

    apply_movement(
        account=account, direction=MovementDirection.OUT, amount=handover.amount,
        movement_type=MovementType.HANDOVER, actor=confirmed_by,
        note=f"Remise confirmée par {confirmed_by}",
    )
    if not account.global_account_id:
        raise ValidationError(_("Cette caisse agent n'est liée à aucune caisse globale."))
    global_account = GlobalCashAccount.objects.select_for_update().get(pk=account.global_account_id)
    if global_account.currency_id != account.currency_id:
        raise ValidationError(_("La devise de la caisse globale ne correspond pas à celle de la caisse agent."))
    _apply_global_movement(
        global_account=global_account, direction=MovementDirection.IN, amount=handover.amount,
        movement_type=GlobalMovementType.HANDOVER, actor=confirmed_by,
        note=f"Remise confirmée depuis la caisse agent #{account.pk}", handover=handover,
    )
    handover.status = HandoverStatus.CONFIRMED
    handover.confirmed_by = confirmed_by
    handover.resolved_at = timezone.now()
    handover.save(update_fields=["status", "confirmed_by", "resolved_at"])
    from apps.finance.events import record_handover
    record_handover(handover, confirmed_by)
    audit_record(actor=confirmed_by, action="HANDOVER_CONFIRM", instance=handover)
    return handover


@ledger_atomic
def close_cash_day(*, account_id: int, business_date, declared_cash: Decimal, closed_by, justification: str = "") -> DailyClosure:
    """Lock the account, freeze the FINCORYA business day and compute its variance."""
    account = CashAccount.objects.select_for_update().get(pk=account_id)
    if not _can_manage_account(closed_by, account, close=True):
        raise PermissionDenied(_("Vous n'êtes pas autorisé à clôturer cette caisse."))
    if declared_cash < 0:
        raise ValidationError(_("Le montant compté ne peut pas être négatif."))

    closure, created = DailyClosure.objects.get_or_create(
        account=account, business_date=business_date,
        defaults={"opening_balance": account.balance},
    )
    if closure.status == ClosureStatus.CLOSED:
        raise ValidationError(_("Cette journée est déjà clôturée."))

    account.is_locked = True
    account.save(update_fields=["is_locked"])

    variance = declared_cash - account.balance
    if variance != 0 and not justification.strip():
        raise ValidationError(_("Une justification est obligatoire lorsque le comptage diffère du solde théorique."))
    closure.expected_balance = account.balance
    closure.declared_cash = declared_cash
    closure.variance = variance
    closure.justification = justification.strip()
    closure.status = ClosureStatus.CLOSED
    closure.closed_by = closed_by
    closure.closed_at = timezone.now()
    closure.save()

    audit_record(
        actor=closed_by, action="CASH_CLOSE", instance=closure,
        after={"expected": str(closure.expected_balance), "declared": str(declared_cash), "variance": str(closure.variance)},
    )

    # Unlock for the next business day (locking only protects the closing instant).
    account.is_locked = False
    account.save(update_fields=["is_locked"])
    if variance:
        apply_movement(
            account=account,
            direction=MovementDirection.IN if variance > 0 else MovementDirection.OUT,
            amount=abs(variance), movement_type=MovementType.ADJUSTMENT,
            actor=closed_by, note=f"Report du compté après clôture {business_date}",
        )
        recipients = {closed_by, *User.objects.filter(role=Role.ADMIN, is_active=True)}
        for recipient in recipients:
            notify(
                recipient=recipient, subject=_("Écart de clôture enregistré"),
                body=f"{account.currency.code}: théorique {closure.expected_balance}, compté {declared_cash}, écart {variance}",
                level="WARNING",
            )
    DailyClosure.objects.get_or_create(
        account=account, business_date=business_date + timedelta(days=1),
        defaults={"opening_balance": declared_cash},
    )
    return closure
