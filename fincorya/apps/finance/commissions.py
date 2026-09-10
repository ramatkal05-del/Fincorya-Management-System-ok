"""Partner-owned dated commission choices and idempotent, cash-neutral conversions."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from apps.accounts.permissions import linked_party
from apps.audit.services import record
from .events import counterpart, line, record_event
from .locking import ledger_atomic
from .models import AccountType, CommissionConversion, CommissionDestination, PartnerCommissionChoice


@ledger_atomic
def choose_commission_destination(*, actor, destination, effective_at=None):
    party = linked_party(actor)
    if party is None or party.type != "PARTNER":
        raise PermissionDenied("Ce choix appartient au partenaire connecté.")
    if destination not in CommissionDestination.values:
        raise ValidationError("Choisissez la destination de vos commissions.")
    now = timezone.now()
    effective_at = effective_at or now
    if effective_at < now:
        raise ValidationError("Un choix ne peut pas modifier rétroactivement les commissions acquises.")
    existing = PartnerCommissionChoice.objects.filter(stakeholder=party, effective_at=effective_at).first()
    if existing:
        if existing.destination != destination:
            raise ValidationError("Un autre choix est déjà enregistré à cette date.")
        return existing
    choice = PartnerCommissionChoice.objects.create(stakeholder=party, chosen_by=actor,
        destination=destination, effective_at=effective_at)
    record(actor=actor, action="PARTNER_COMMISSION_CHOICE", instance=choice,
        after={"destination": destination, "effective_at": effective_at.isoformat()})
    return choice


@ledger_atomic
def convert_automatic_commission(*, operation, party, amount, actor):
    existing = CommissionConversion.objects.filter(operation=operation).first()
    if existing:
        return existing
    choice = PartnerCommissionChoice.objects.filter(stakeholder=party, effective_at__lte=operation.created_at).first()
    if not choice or choice.destination != CommissionDestination.GUARANTEE or amount <= 0:
        return None
    batch = record_event(actor=actor, source=operation, event="COMMISSION_AUTO_CONVERT", effective_at=operation.created_at,
        description=f"Conversion automatique des commissions - choix #{choice.pk}",
        lines=[line(counterpart(operation.currency, AccountType.PARTNER_PAYABLE, party=party), "DEBIT", amount),
               line(counterpart(operation.currency, AccountType.PARTNER_GUARANTEE, party=party), "CREDIT", amount)])
    conversion = CommissionConversion.objects.create(operation=operation, choice=choice, amount=amount, batch=batch)
    record(actor=actor, action="COMMISSION_AUTO_CONVERT", instance=conversion,
        after={"choice": choice.pk, "amount": str(amount), "currency": operation.currency.code})
    return conversion


def reverse_automatic_commission(*, operation, actor, reason):
    from .services import ledger_balance, reverse_batch
    conversion = CommissionConversion.objects.filter(operation=operation).select_related("choice__stakeholder").first()
    if conversion:
        guarantee = counterpart(operation.currency, AccountType.PARTNER_GUARANTEE, party=conversion.choice.stakeholder)
        if ledger_balance(guarantee) < conversion.amount:
            raise ValidationError("Garantie convertie indisponible : une régularisation documentée est nécessaire avant l’annulation.")
        reverse_batch(batch_id=conversion.batch_id, actor=actor, reason=reason,
                      idempotency_key=f"cancel-auto-commission-{operation.pk}")
