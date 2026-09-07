"""
FINCORYA transactional operation services. Business guarantees:
  - fee is looked up from the tariff tier matching (zone/schedule, amount)
  - a manual fee may only reduce the automatic fee, never raise it
  - amount/fee are also stored converted to USD (system reference currency)
  - a withdrawal is refused if the cash account can't cover it
  - every operation is mirrored by exactly one cash movement while it is
    open; cancelling reverses that movement

Concurrency guarantees required by sections 6 and 11:
  - `select_for_update()` on the cash account for the whole operation
  - "reçu" transfers are split into a receive step (no cash movement,
    status PENDING) and a pay step (`pay_received_transfer`, single
    payout, refuses to pay twice) instead of being one atomic action
  - corrections go through `revise_operation`, blocked once the owning
    day is closed, and always leave a paper trail
"""
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.audit.services import record as audit_record
from apps.cash.models import CashAccount, ClosureStatus, DailyClosure, MovementDirection, MovementType
from apps.cash.services import apply_movement
from apps.accounts.models import Role
from apps.notifications.services import notify
from apps.pricing.services import lookup_fee, resolve_fee, current_rate_to_usd, from_usd
from config.business_time import business_date

from .models import Operation, OperationRequest, OperationRevision, OperationStatus, OperationType


def _day_is_closed(account: CashAccount, on_date) -> bool:
    return DailyClosure.objects.filter(account=account, business_date=on_date, status=ClosureStatus.CLOSED).exists()


def _assert_account_access(actor, account):
    if not account.is_active:
        raise ValidationError(_("Cette caisse n'a pas encore été allouée par l'administrateur."))
    if actor.role == Role.ADMIN:
        return
    if actor.role == Role.AGENT and account.agent_id == actor.pk:
        return
    raise PermissionDenied(_("Vous n'êtes pas autorisé à utiliser cette caisse."))


def _customer_fields(service, customer_identifier, customer_name):
    identifier = (customer_identifier or "").strip()
    name = (customer_name or "").strip()
    return {"service": service, "customer_identifier": identifier, "customer_name": name}


def _pricing(*, account, amount, tariff_schedule, manual_fee, actor, fee_justification=""):
    rate = current_rate_to_usd(account.currency)
    amount_usd = (amount * rate).quantize(Decimal("0.01"))
    if tariff_schedule.currency.code != "USD":
        raise ValidationError(_("La grille tarifaire de référence doit être exprimée en USD."))
    if amount_usd > Decimal("5000.00"):
        if actor.role != Role.ADMIN or manual_fee is None or not fee_justification.strip():
            raise ValidationError(_("Au-delà de 5 000 USD, un administrateur doit saisir les frais et leur justification."))
        fee_usd = Decimal(manual_fee).quantize(Decimal("0.01"))
        if fee_usd <= 0:
            raise ValidationError(_("Les frais administrateur doivent être supérieurs à zéro."))
        fee_auto_usd = Decimal("0.00")
    else:
        fee_auto_usd = resolve_fee(tariff_schedule, amount_usd)
        fee_usd = resolve_fee(tariff_schedule, amount_usd, manual_fee)
    return {
        "rate": rate, "amount_usd": amount_usd,
        "fee_usd": fee_usd, "fee": from_usd(fee_usd, account.currency),
        "fee_auto": from_usd(fee_auto_usd, account.currency),
    }


def _notify_operation(operation, actor, action):
    recipients = {actor}
    from apps.accounts.models import User
    recipients.update(User.objects.filter(role=Role.ADMIN, is_active=True))
    level = "WARNING" if operation.amount_usd >= Decimal("1000.00") else "INFO"
    for recipient in recipients:
        notify(recipient=recipient, subject=action, body=f"{operation.reference} · {operation.amount} {operation.currency.code}", level=level)


def _claim_request(actor, key, action):
    if not key:
        return None, None
    if len(key) > 64:
        raise ValidationError(_("La clé d'idempotence est trop longue."))
    try:
        with transaction.atomic():
            request, _ = OperationRequest.objects.get_or_create(actor=actor, key=key, defaults={"action": action})
    except IntegrityError:
        # A concurrent request won the unique (actor, key) reservation.
        request = OperationRequest.objects.get(actor=actor, key=key)
    request = OperationRequest.objects.select_for_update().get(pk=request.pk)
    if request.action != action:
        raise ValidationError(_("Cette clé d'idempotence a déjà été utilisée pour une autre action."))
    return request, request.operation


def _complete_request(request, operation):
    if request:
        request.operation = operation
        request.save(update_fields=["operation"])


@transaction.atomic
def create_sent_transfer(*, agent, account_id: int, amount: Decimal, tariff_schedule,
                          manual_fee: Decimal | None = None, note: str = "", fee_justification: str = "", idempotency_key: str | None = None,
                          service="AIRTEL_MONEY", customer_identifier="", customer_name="") -> Operation:
    """Register a FINCORYA sent transfer and credit the cash account."""
    if amount <= 0:
        raise ValidationError(_("Le montant doit être supérieur à zéro."))
    request, existing = _claim_request(agent, idempotency_key, OperationType.SENT_TRANSFER)
    if existing:
        return existing

    account = CashAccount.objects.select_for_update().get(pk=account_id)
    _assert_account_access(agent, account)
    pricing = _pricing(account=account, amount=amount, tariff_schedule=tariff_schedule, manual_fee=manual_fee, actor=agent, fee_justification=fee_justification)

    operation = Operation.objects.create(
        type=OperationType.SENT_TRANSFER, status=OperationStatus.COMPLETED,
        agent=agent, account=account, currency=account.currency, tariff_schedule=tariff_schedule,
        amount=amount, fee=pricing["fee"], fee_auto=pricing["fee_auto"], rate_to_usd=pricing["rate"],
        amount_usd=pricing["amount_usd"], fee_usd=pricing["fee_usd"],
        note=note, paid_at=timezone.now(), **_customer_fields(service, customer_identifier, customer_name),
    )
    apply_movement(
        account=account, direction=MovementDirection.IN, amount=amount + pricing["fee"],
        movement_type=MovementType.OPERATION, actor=agent, operation=operation,
        note=f"Transfert envoyé {operation.reference}",
    )
    audit_record(actor=agent, action="OPERATION_CREATE", instance=operation, after={"type": operation.type, "amount": str(amount)})
    _notify_operation(operation, agent, _("Transfert envoyé enregistré"))
    _complete_request(request, operation)
    return operation


@transaction.atomic
def receive_transfer(*, agent, account_id: int, amount: Decimal, tariff_schedule,
                      manual_fee: Decimal | None = None, note: str = "", fee_justification: str = "", idempotency_key: str | None = None,
                      service="AIRTEL_MONEY", customer_identifier="", customer_name="") -> Operation:
    """Registers an incoming payout request. No cash movement yet — status PENDING until paid."""
    if amount <= 0:
        raise ValidationError(_("Le montant doit être supérieur à zéro."))
    request, existing = _claim_request(agent, idempotency_key, OperationType.RECEIVED_TRANSFER)
    if existing:
        return existing

    account = CashAccount.objects.select_for_update().get(pk=account_id)
    _assert_account_access(agent, account)
    pricing = _pricing(account=account, amount=amount, tariff_schedule=tariff_schedule, manual_fee=manual_fee, actor=agent, fee_justification=fee_justification)

    operation = Operation.objects.create(
        type=OperationType.RECEIVED_TRANSFER, status=OperationStatus.PENDING,
        agent=agent, account=account, currency=account.currency, tariff_schedule=tariff_schedule,
        amount=amount, fee=pricing["fee"], fee_auto=pricing["fee_auto"], rate_to_usd=pricing["rate"],
        amount_usd=pricing["amount_usd"], fee_usd=pricing["fee_usd"],
        note=note, **_customer_fields(service, customer_identifier, customer_name),
    )
    audit_record(actor=agent, action="OPERATION_CREATE", instance=operation, after={"type": operation.type, "amount": str(amount)})
    _notify_operation(operation, agent, _("Transfert reçu en attente"))
    _complete_request(request, operation)
    return operation


@transaction.atomic
def pay_received_transfer(*, operation_id: int, paid_by) -> Operation:
    """Pays out a PENDING received transfer exactly once (section 11: 'Double paiement')."""
    operation = Operation.objects.select_for_update().get(pk=operation_id)
    if operation.status != OperationStatus.PENDING:
        raise ValidationError(_("Ce reçu a déjà été payé ou n'est plus payable."))

    account = CashAccount.objects.select_for_update().get(pk=operation.account_id)
    _assert_account_access(paid_by, account)
    cash_impact = operation.amount - operation.fee
    if cash_impact <= 0:
        raise ValidationError(_("Les frais doivent être inférieurs au montant payé."))
    if account.balance < cash_impact:
        raise ValidationError(_("Fonds insuffisants dans la caisse pour ce paiement."))

    apply_movement(
        account=account, direction=MovementDirection.OUT, amount=cash_impact,
        movement_type=MovementType.OPERATION, actor=paid_by, operation=operation,
        note=f"Paiement reçu {operation.reference}",
    )
    operation.status = OperationStatus.COMPLETED
    operation.paid_at = timezone.now()
    operation.save(update_fields=["status", "paid_at"])
    audit_record(actor=paid_by, action="OPERATION_PAY", instance=operation)
    _notify_operation(operation, paid_by, _("Transfert reçu payé"))
    return operation


@transaction.atomic
def create_withdrawal(*, agent, account_id: int, amount: Decimal, tariff_schedule,
                       manual_fee: Decimal | None = None, note: str = "", fee_justification: str = "", idempotency_key: str | None = None,
                       service="AIRTEL_MONEY", customer_identifier="", customer_name="") -> Operation:
    """Register a FINCORYA withdrawal and debit the cash account."""
    if amount <= 0:
        raise ValidationError(_("Le montant doit être supérieur à zéro."))
    request, existing = _claim_request(agent, idempotency_key, OperationType.WITHDRAWAL)
    if existing:
        return existing

    account = CashAccount.objects.select_for_update().get(pk=account_id)
    _assert_account_access(agent, account)
    pricing = _pricing(account=account, amount=amount, tariff_schedule=tariff_schedule, manual_fee=manual_fee, actor=agent, fee_justification=fee_justification)
    cash_impact = amount - pricing["fee"]
    if cash_impact <= 0 or account.balance < cash_impact:
        raise ValidationError(_("Fonds insuffisants dans la caisse pour ce retrait."))

    operation = Operation.objects.create(
        type=OperationType.WITHDRAWAL, status=OperationStatus.COMPLETED,
        agent=agent, account=account, currency=account.currency, tariff_schedule=tariff_schedule,
        amount=amount, fee=pricing["fee"], fee_auto=pricing["fee_auto"], rate_to_usd=pricing["rate"],
        amount_usd=pricing["amount_usd"], fee_usd=pricing["fee_usd"],
        note=note, paid_at=timezone.now(), **_customer_fields(service, customer_identifier, customer_name),
    )
    apply_movement(
        account=account, direction=MovementDirection.OUT, amount=cash_impact,
        movement_type=MovementType.OPERATION, actor=agent, operation=operation,
        note=f"Retrait {operation.reference}",
    )
    audit_record(actor=agent, action="OPERATION_CREATE", instance=operation, after={"type": operation.type, "amount": str(amount)})
    _notify_operation(operation, agent, _("Retrait payé enregistré"))
    _complete_request(request, operation)
    return operation


@transaction.atomic
def cancel_operation(*, operation_id: int, cancelled_by, reason: str) -> Operation:
    """Cancel an operation with a locked, auditable counter-movement."""
    operation = Operation.objects.select_for_update().get(pk=operation_id)
    if operation.status != OperationStatus.COMPLETED:
        raise ValidationError(_("Seule une opération terminée peut être annulée."))

    account = CashAccount.objects.select_for_update().get(pk=operation.account_id)
    _assert_account_access(cancelled_by, account)
    if not reason.strip():
        raise ValidationError(_("Le motif d'annulation est obligatoire."))
    if _day_is_closed(account, business_date(operation.created_at)):
        raise ValidationError(_("La journée de cette opération est déjà clôturée ; utilisez un ajustement."))

    original = operation.movements.filter(movement_type=MovementType.OPERATION).first()
    if original is None:
        raise ValidationError(_("L'écriture de caisse d'origine est introuvable ; annulation bloquée."))
    reverse_direction = MovementDirection.OUT if original.direction == MovementDirection.IN else MovementDirection.IN
    apply_movement(
        account=account, direction=reverse_direction, amount=original.amount,
        movement_type=MovementType.ADJUSTMENT, actor=cancelled_by, operation=operation,
        note=f"Annulation {operation.reference}: {reason}",
    )
    operation.status = OperationStatus.CANCELLED
    operation.cancel_reason = reason.strip()
    operation.cancelled_at = timezone.now()
    operation.save(update_fields=["status", "cancel_reason", "cancelled_at"])
    audit_record(actor=cancelled_by, action="OPERATION_CANCEL", instance=operation, before={"reason": reason})
    _notify_operation(operation, cancelled_by, _("Opération annulée"))
    return operation


@transaction.atomic
def revise_operation(*, operation_id: int, changes: dict, reason: str, revised_by) -> Operation:
    """Allowed only before the owning business day is closed (section 6: 'Avant clôture seulement')."""
    operation = Operation.objects.select_for_update().get(pk=operation_id)
    if operation.status == OperationStatus.CANCELLED:
        raise ValidationError(_("Une opération annulée ne peut plus être corrigée."))
    account = CashAccount.objects.select_for_update().get(pk=operation.account_id)
    _assert_account_access(revised_by, account)
    if not reason.strip():
        raise ValidationError(_("Le motif de correction est obligatoire."))
    if _day_is_closed(account, business_date(operation.created_at)):
        raise ValidationError(_("Cette opération appartient à une journée déjà clôturée."))

    allowed = {"amount", "fee", "note"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValidationError(_("Champs de correction interdits : %(fields)s") % {"fields": ", ".join(sorted(unknown))})
    new_amount = Decimal(changes.get("amount", operation.amount)).quantize(Decimal("0.01"))
    new_fee = Decimal(changes.get("fee", operation.fee)).quantize(Decimal("0.01"))
    if new_amount <= 0 or new_fee < 0:
        raise ValidationError(_("Le montant doit être positif et les frais non négatifs."))
    new_amount_usd = (new_amount * operation.rate_to_usd).quantize(Decimal("0.01"))
    if new_amount_usd > Decimal("5000.00") and revised_by.role != Role.ADMIN:
        raise ValidationError(_("Seul un administrateur peut corriger une opération supérieure à 5 000 USD."))
    auto_fee_usd = lookup_fee(operation.tariff_schedule, new_amount_usd)
    new_fee_usd = (new_fee * operation.rate_to_usd).quantize(Decimal("0.01"))
    if new_amount_usd <= Decimal("5000.00") and new_fee_usd > auto_fee_usd:
        raise ValidationError(_("Les frais corrigés ne peuvent pas dépasser les frais automatiques."))
    new_fee_auto = (auto_fee_usd / operation.rate_to_usd).quantize(Decimal("0.01"))

    before = {"amount": str(operation.amount), "fee": str(operation.fee), "note": operation.note}
    original = operation.movements.filter(movement_type=MovementType.OPERATION).first()
    if operation.status == OperationStatus.COMPLETED:
        if original is None:
            raise ValidationError(_("L'écriture de caisse d'origine est introuvable ; correction bloquée."))
        old_signed = original.amount if original.direction == MovementDirection.IN else -original.amount
        new_impact = new_amount + new_fee if operation.type == OperationType.SENT_TRANSFER else new_amount - new_fee
        if new_impact <= 0:
            raise ValidationError(_("L'impact de caisse corrigé doit être positif."))
        new_signed = new_impact if operation.type == OperationType.SENT_TRANSFER else -new_impact
        delta = new_signed - old_signed
        if delta:
            apply_movement(
                account=account, direction=MovementDirection.IN if delta > 0 else MovementDirection.OUT,
                amount=abs(delta), movement_type=MovementType.ADJUSTMENT, actor=revised_by,
                operation=operation, note=f"Correction {operation.reference}: {reason.strip()}",
            )
    operation.amount = new_amount
    operation.fee = new_fee
    operation.fee_auto = new_fee_auto
    operation.amount_usd = new_amount_usd
    operation.fee_usd = new_fee_usd
    operation.note = changes.get("note", operation.note)
    operation.save(update_fields=["amount", "fee", "fee_auto", "amount_usd", "fee_usd", "note"])
    after = {"amount": str(operation.amount), "fee": str(operation.fee), "note": operation.note}

    OperationRevision.objects.create(operation=operation, before=before, after=after, reason=reason.strip(), revised_by=revised_by)
    audit_record(actor=revised_by, action="OPERATION_REVISE", instance=operation, before=before, after=after)
    _notify_operation(operation, revised_by, _("Opération corrigée"))
    return operation
