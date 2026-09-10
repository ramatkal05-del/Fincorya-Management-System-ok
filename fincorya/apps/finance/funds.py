"""Origin, allocation and movement of funds; stakeholder requests.

Every function here produces exactly one balanced journal batch per business
event and is idempotent on its client key. Nothing in this module changes a
balance outside the ledger.
"""
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.accounts.permissions import finance_policy, require_finance_access
from apps.audit.services import record as audit_record
from apps.notifications.services import notify
from config.business_time import business_day_bounds
from .cutover import active_cutover
from .events import _project_cash, _validate_cash_payment, counterpart, line, record_event
from .locking import ledger_atomic
from .models import (AccountNature, AccountType, FinancialAccount, FundContribution, FundOrigin, InternalTransfer,
                     JournalBatch, PartnerGuarantee, RequestKind, RequestStatus, StakeholderRequest, TransferStatus)
from .services import ledger_balance

TREASURY_TYPES = {"AGENT_CASH", "GLOBAL_CASH", "MOBILE_MONEY", "DIGITAL", "FINANCIAL_SERVICE"}

ORIGIN_ACCOUNT = {
    FundOrigin.SHAREHOLDER: AccountType.CAPITAL,
    FundOrigin.INVESTOR: AccountType.INVESTOR_FUNDS,
    FundOrigin.PARTNER_GUARANTEE: AccountType.PARTNER_GUARANTEE,
}
ORIGIN_PARTY_TYPE = {FundOrigin.SHAREHOLDER: "SHAREHOLDER", FundOrigin.INVESTOR: "INVESTOR", FundOrigin.PARTNER_GUARANTEE: "PARTNER"}


def _treasury(account, currency_id):
    if account.nature != AccountNature.ASSET or account.account_type not in TREASURY_TYPES or not account.is_active:
        raise ValidationError("Sélectionnez un compte de trésorerie actif.")
    if account.currency_id != currency_id:
        raise ValidationError("La devise du compte ne correspond pas à celle du mouvement.")
    return account


def _amount(value):
    amount = Decimal(value).quantize(Decimal("0.01"))
    if amount <= 0:
        raise ValidationError("Le montant doit être strictement positif.")
    return amount


def _engine_required():
    if not active_cutover():
        raise ValidationError("Le moteur financier est désactivé.")


def _effective(on_date):
    start, _ = business_day_bounds(on_date, on_date)
    now = timezone.now()
    return now if start.date() == now.date() else start


# --------------------------------------------------------------------------- contributions

def opening_equity(currency):
    account, _ = FinancialAccount.objects.get_or_create(code=f"OPENING-EQUITY-{currency.code}", defaults={
        "name": f"Solde d’ouverture {currency.code}", "account_type": AccountType.CAPITAL, "nature": AccountNature.EQUITY, "currency": currency})
    return account


@ledger_atomic
def record_contribution(*, actor, client_key, origin, stakeholder_id, amount, currency, received_on, account_id=None,
                        external_reference="", note="", from_opening_balances=False, evidence=None):
    """Record a received contribution once: treasury in, origin account credited.

    With `from_opening_balances`, the funds were already inside the reconciled opening balances: the
    entry only reclassifies opening equity into the party's account, so no cash is received twice.
    """
    require_finance_access(actor, "approve")
    _engine_required()
    existing = FundContribution.objects.filter(client_key=client_key).first()
    if existing:
        if (existing.origin, existing.stakeholder_id, existing.amount, existing.currency_id) != (origin, stakeholder_id, _amount(amount), currency.pk):
            raise ValidationError("Cette clé désigne un autre apport.")
        return existing
    from apps.stakeholders.models import Stakeholder
    party = Stakeholder.objects.get(pk=stakeholder_id)
    if party.type != ORIGIN_PARTY_TYPE[origin]:
        raise ValidationError("L’origine des fonds ne correspond pas au type de la partie.")
    amount = _amount(amount)
    if from_opening_balances:
        cash = opening_equity(currency)
        if ledger_balance(cash) < amount:
            raise ValidationError("Les soldes de reprise ne contiennent pas ce montant ; il s’agit alors d’un nouveau versement.")
    else:
        if account_id is None:
            raise ValidationError("Indiquez le compte de trésorerie qui a reçu les fonds.")
        cash = _treasury(FinancialAccount.objects.select_for_update().get(pk=account_id), currency.pk)
    contribution = FundContribution.objects.create(
        client_key=client_key, origin=origin, stakeholder=party, amount=amount, currency=currency, received_on=received_on,
        receiving_account=cash, from_opening_balances=from_opening_balances, external_reference=external_reference, note=note,
        evidence=evidence, created_by=actor)
    contribution.batch = record_event(actor=actor, source=contribution, event="CONTRIBUTION", effective_at=_effective(received_on),
        description=f"{FundOrigin(origin).label} {party.name}" + (" (reclassement de la reprise)" if from_opening_balances else ""),
        lines=[line(cash, "DEBIT", amount), line(counterpart(currency, ORIGIN_ACCOUNT[origin], party=party), "CREDIT", amount)])
    contribution.save(update_fields=["batch"])
    if not from_opening_balances:
        _project_cash(cash, "IN", amount, actor, f"Apport #{contribution.pk}")
    audit_record(actor=actor, action="FUND_CONTRIBUTION", instance=contribution, after={"origin": origin, "amount": str(contribution.amount)})
    return contribution


def contributed_total(party, currency):
    """Cumulative funds a party still has in the company, read from the ledger."""
    return sum((ledger_balance(account) for account in FinancialAccount.objects.filter(
        economic_owner=party, currency=currency, account_type__in=[AccountType.CAPITAL, AccountType.INVESTOR_FUNDS, AccountType.PARTNER_GUARANTEE])), Decimal("0"))


# --------------------------------------------------------------------------- internal transfers

@ledger_atomic
def initiate_transfer(*, actor, client_key, source_id, destination_id, amount, fee=0, note=""):
    """Take funds out of the source; they sit in transit until confirmed."""
    require_transfer_access(actor)
    _engine_required()
    existing = InternalTransfer.objects.filter(client_key=client_key).first()
    if existing:
        if (existing.initiated_by_id, existing.source_id, existing.destination_id, existing.amount, existing.fee) != (
                actor.pk, source_id, destination_id, _amount(amount), Decimal(fee or 0)):
            raise ValidationError("Cette clé désigne un autre transfert.")
        return existing
    source = FinancialAccount.objects.select_for_update().get(pk=source_id)
    destination = FinancialAccount.objects.select_for_update().get(pk=destination_id)
    if finance_policy(actor).own_cash_only:
        if (source.account_type != AccountType.AGENT_CASH or source.responsible_user_id != actor.pk
                or destination.account_type != AccountType.AGENT_CASH
                or not destination.responsible_user_id or destination.responsible_user_id == actor.pk):
            raise PermissionDenied("Un agent transfère uniquement de sa propre caisse vers celle d’un autre agent.")
    if source.pk == destination.pk:
        raise ValidationError("La source et la destination doivent être différentes.")
    _treasury(source, source.currency_id)
    _treasury(destination, source.currency_id)
    amount, fee = _amount(amount), Decimal(fee or 0).quantize(Decimal("0.01"))
    if fee < 0:
        raise ValidationError("Les frais de transfert ne peuvent pas être négatifs.")
    _validate_cash_payment(source, source.currency_id, amount + fee)
    transfer = InternalTransfer.objects.create(client_key=client_key, source=source, destination=destination, currency=source.currency,
        amount=amount, fee=fee, note=note, initiated_by=actor, initiated_at=timezone.now())
    for admin in User.objects.filter(role=Role.ADMIN, is_active=True):
        notify(recipient=admin, subject="Transfert interne initié",
               body=f"Transfert #{transfer.pk} de {amount} {source.currency.code} de {source.code} vers {destination.code} est en transit. Validation finale requise après réception.",
               level="INFO")
    # TRANSIT is a liability-natured account: a DEBIT there represents funds in flight that we still own.
    lines = [line(source, "CREDIT", amount + fee), line(counterpart(source.currency, AccountType.TRANSIT), "DEBIT", amount)]
    if fee:
        lines.append(line(counterpart(source.currency, AccountType.EXPENSE, category="TRANSFER_FEE"), "DEBIT", fee))
    transfer.initiation_batch = record_event(actor=actor, source=transfer, event="TRANSFER_INITIATE", effective_at=transfer.initiated_at, lines=lines)
    transfer.save(update_fields=["initiation_batch"])
    _project_cash(source, "OUT", amount + fee, actor, f"Transfert #{transfer.pk}")
    audit_record(actor=actor, action="TRANSFER_INITIATE", instance=transfer, after={"amount": str(amount), "fee": str(fee)})
    return transfer


@ledger_atomic
def receive_transfer(*, actor, transfer_id):
    """Mark an in-transit transfer as physically received, keeping funds unavailable until admin final validation."""
    require_transfer_access(actor)
    transfer = InternalTransfer.objects.select_for_update().get(pk=transfer_id)
    if finance_policy(actor).own_cash_only and transfer.destination.responsible_user_id != actor.pk:
        raise PermissionDenied("Seul l’agent destinataire peut confirmer cette réception.")
    if transfer.return_confirmed_at:
        raise ValidationError("Le retour des fonds est déjà confirmé.")
    if transfer.status == TransferStatus.RECEIVED:
        return transfer
    if transfer.status != TransferStatus.INITIATED:
        raise ValidationError("Seul un transfert en transit peut être marqué comme reçu.")
    transfer.received_by, transfer.received_at = actor, timezone.now()
    transfer.status = TransferStatus.RECEIVED
    transfer.save(update_fields=["received_by", "received_at", "status"])
    for admin in User.objects.filter(role=Role.ADMIN, is_active=True):
        notify(recipient=admin, subject="Transfert reçu — validation finale requise",
               body=f"Transfert #{transfer.pk} de {transfer.amount} {transfer.currency.code} vers {transfer.destination.code} a été marqué reçu. Validation finale par un admin nécessaire.",
               level="WARNING")
    audit_record(actor=actor, action="TRANSFER_RECEIVE", instance=transfer)
    return transfer


@ledger_atomic
def confirm_transfer(*, actor, transfer_id):
    """Final admin validation: credit the destination and release funds."""
    require_finance_access(actor, "approve")
    transfer = InternalTransfer.objects.select_for_update().get(pk=transfer_id)
    if transfer.status == TransferStatus.CONFIRMED:
        return transfer
    if transfer.status != TransferStatus.RECEIVED or transfer.return_confirmed_at:
        raise ValidationError("Seul un transfert reçu peut être validé finalement.")
    destination = FinancialAccount.objects.select_for_update().get(pk=transfer.destination_id)
    transfer.confirmed_by, transfer.confirmed_at = actor, timezone.now()
    transfer.settlement_batch = record_event(actor=actor, source=transfer, event="TRANSFER_CONFIRM", effective_at=transfer.confirmed_at,
        lines=[line(destination, "DEBIT", transfer.amount), line(counterpart(transfer.currency, AccountType.TRANSIT), "CREDIT", transfer.amount)])
    transfer.status = TransferStatus.CONFIRMED
    transfer.save(update_fields=["confirmed_by", "confirmed_at", "settlement_batch", "status"])
    _project_cash(destination, "IN", transfer.amount, actor, f"Transfert #{transfer.pk}")
    audit_record(actor=actor, action="TRANSFER_CONFIRM", instance=transfer)
    return transfer


@ledger_atomic
def cancel_transfer(*, actor, transfer_id, reason):
    """Return in-transit funds to the source; the fee, if charged, stays a recorded expense."""
    require_finance_access(actor, "approve")
    transfer = InternalTransfer.objects.select_for_update().get(pk=transfer_id)
    if transfer.status == TransferStatus.CANCELLED:
        return transfer
    if transfer.status not in {TransferStatus.INITIATED, TransferStatus.RECEIVED} or not reason.strip():
        raise ValidationError("Seul un transfert non validé peut être annulé, avec un motif.")
    if not transfer.return_confirmed_at:
        raise ValidationError("Confirmez le retour effectif des fonds avec un justificatif avant tout recrédit.")
    source = FinancialAccount.objects.select_for_update().get(pk=transfer.source_id)
    transfer.settlement_batch = record_event(actor=actor, source=transfer, event="TRANSFER_CANCEL", effective_at=timezone.now(),
        description=f"Annulation transfert #{transfer.pk}: {reason.strip()}",
        lines=[line(source, "DEBIT", transfer.amount), line(counterpart(transfer.currency, AccountType.TRANSIT), "CREDIT", transfer.amount)])
    transfer.status = TransferStatus.CANCELLED
    transfer.save(update_fields=["settlement_batch", "status"])
    _project_cash(source, "IN", transfer.amount, actor, f"Annulation transfert #{transfer.pk}")
    audit_record(actor=actor, action="TRANSFER_CANCEL", instance=transfer, after={"reason": reason})
    return transfer


def in_transit(currency):
    return ledger_balance(counterpart(currency, AccountType.TRANSIT)) * -1


def require_transfer_access(actor):
    policy = finance_policy(actor)
    if not (policy.prepare or policy.own_cash_only):
        raise PermissionDenied("Vous ne pouvez pas effectuer de transfert interne.")
    return policy


@ledger_atomic
def confirm_transfer_return(*, actor, transfer_id, note):
    """Record physical return to the source; only admin cancellation releases transit."""
    policy = require_transfer_access(actor)
    transfer = InternalTransfer.objects.select_for_update().get(pk=transfer_id)
    if policy.own_cash_only and transfer.source.responsible_user_id != actor.pk:
        raise PermissionDenied("Seul l’agent expéditeur peut confirmer le retour dans sa caisse.")
    if transfer.status not in {TransferStatus.INITIATED, TransferStatus.RECEIVED}:
        raise ValidationError("Ce transfert est déjà terminé.")
    if transfer.return_confirmed_at:
        return transfer
    if len(note.strip()) < 5:
        raise ValidationError("Documentez le retour effectif : reçu, référence ou justification vérifiée.")
    transfer.return_confirmed_by, transfer.return_confirmed_at = actor, timezone.now()
    transfer.return_note = note.strip()
    transfer.save(update_fields=["return_confirmed_by", "return_confirmed_at", "return_note"])
    audit_record(actor=actor, action="TRANSFER_RETURN", instance=transfer, after={"note": note.strip()})
    for admin in User.objects.filter(role=Role.ADMIN, is_active=True):
        notify(recipient=admin, subject="Retour de transfert confirmé", body=f"Transfert #{transfer.pk} : retour confirmé. Annulation à valider.")
    return transfer


# --------------------------------------------------------------------------- partners

def guarantee_balance(party, currency):
    return ledger_balance(counterpart(currency, AccountType.PARTNER_GUARANTEE, party=party))


def assert_partner_ceiling(party, operation):
    """Active funded guarantee and configured ceiling; exposure may exceed the guarantee."""
    guarantee = PartnerGuarantee.objects.filter(stakeholder=party, is_active=True).select_related("currency").first()
    if guarantee is None:
        raise ValidationError(f"{party.name} : aucune garantie active ; plafond par opération non défini.")
    if operation.amount_usd > ceiling_limit():
        raise ValidationError(f"Une opération partenaire ne peut pas dépasser {ceiling_limit()} USD.")
    if guarantee.currency_id == operation.currency_id:
        exposure = operation.amount
    elif guarantee.currency.code == "USD":
        exposure = operation.amount_usd
    else:
        raise ValidationError("La devise de la garantie ne permet pas de contrôler cette opération.")
    if exposure > guarantee.per_operation_ceiling:
        raise ValidationError(f"Opération de {exposure} {guarantee.currency.code} au-dessus du plafond partenaire ({guarantee.per_operation_ceiling}).")
    held = guarantee_balance(party, guarantee.currency)
    if held <= 0:
        raise ValidationError("La garantie active doit disposer d’un solde positif.")


def ceiling_limit():
    from django.conf import settings
    return min(Decimal("2000"), Decimal(str(getattr(settings, "PARTNER_CEILING_MAX_USD", "2000"))))


@ledger_atomic
def set_partner_guarantee(*, actor, stakeholder_id, currency, per_operation_ceiling, is_active=True):
    """The admin sets the ceiling explicitly; it is never derived from the deposit, and it is capped."""
    require_finance_access(actor, "administer")
    from apps.stakeholders.models import Stakeholder
    party = Stakeholder.objects.get(pk=stakeholder_id, type="PARTNER")
    ceiling = _amount(per_operation_ceiling)
    from apps.pricing.services import to_usd
    if to_usd(ceiling, currency) > ceiling_limit():
        raise ValidationError(f"Le plafond par opération ne peut pas dépasser {ceiling_limit()} USD.")
    guarantee, _ = PartnerGuarantee.objects.update_or_create(stakeholder=party, defaults={
        "currency": currency, "per_operation_ceiling": ceiling, "is_active": is_active, "updated_by": actor})
    audit_record(actor=actor, action="PARTNER_GUARANTEE_SET", instance=guarantee, after={"ceiling": str(ceiling), "active": is_active})
    return guarantee


@ledger_atomic
def onboard_party(*, actor, party_type, identity, currency=None, per_operation_ceiling=None, deposit=None):
    """Admin-driven creation of a shareholder / investor / partner with its real, explicitly entered funds.

    `identity`: dict of Stakeholder fields. `deposit`: None or {client_key, amount, received_on, account_id,
    from_opening_balances, external_reference, note, evidence}. Nothing is prefilled or assumed.
    """
    require_finance_access(actor, "administer")
    from apps.stakeholders.models import Stakeholder
    party = Stakeholder(type=party_type, **identity)
    party.full_clean(exclude=["canonical_identity"])
    party.save()
    audit_record(actor=actor, action="FINANCE_CREATE", instance=party, after={"type": party_type})
    guarantee = None
    if party_type == "PARTNER":
        if currency is None or per_operation_ceiling is None:
            raise ValidationError("Un partenaire exige la devise de sa garantie et son plafond par opération.")
        guarantee = set_partner_guarantee(actor=actor, stakeholder_id=party.pk, currency=currency, per_operation_ceiling=per_operation_ceiling)
    contribution = None
    if deposit:
        origin = {"PARTNER": FundOrigin.PARTNER_GUARANTEE, "INVESTOR": FundOrigin.INVESTOR, "SHAREHOLDER": FundOrigin.SHAREHOLDER}[party_type]
        contribution = record_contribution(actor=actor, origin=origin, stakeholder_id=party.pk, currency=currency, **deposit)
    return party, guarantee, contribution


# --------------------------------------------------------------------------- agent cash boxes after cutover

@ledger_atomic
def open_agent_cash(*, actor, agent, currency, global_account=None, system=False):
    """Create an empty logical cash box for an agent (one per currency) linked to the ledger. No balance is created.

    `system=True` is used by the ledger adapter when an empty legacy box is first funded after the cutover.
    """
    if not system:
        require_finance_access(actor, "prepare")
    _engine_required()
    from apps.cash.models import CashAccount, GlobalCashAccount
    from apps.accounts.models import Role
    if agent.role != Role.AGENT or not agent.is_active:
        raise ValidationError("La caisse doit appartenir à un agent actif.")
    if global_account is None:
        global_account = GlobalCashAccount.objects.filter(currency=currency, is_active=True).first()
        if global_account is None:
            raise ValidationError(f"Aucune caisse globale {currency.code} ne peut alimenter cette caisse.")
    legacy, created = CashAccount.objects.get_or_create(agent=agent, currency=currency, defaults={"global_account": global_account})
    if legacy.balance or legacy.movements.exists():
        raise ValidationError("Cette caisse a déjà un historique ; elle doit passer par la reprise réconciliée.")
    account = FinancialAccount.objects.filter(legacy_cash_account=legacy).first()
    if account is None:
        account = FinancialAccount.objects.create(legacy_cash_account=legacy, code=f"CASH-{agent.pk}-{currency.code}", name=f"Caisse {agent.get_full_name() or agent.email} {currency.code}",
            account_type=AccountType.AGENT_CASH, nature=AccountNature.ASSET, currency=currency, responsible_user=agent, cutover_at=timezone.now())
        audit_record(actor=actor, action="AGENT_CASH_OPEN", instance=account, after={"agent": agent.pk, "currency": currency.code, "created_legacy": created})
    return account


# --------------------------------------------------------------------------- currency conversions

@ledger_atomic
def record_conversion(*, actor, client_key, source_id, destination_id, amount_source, amount_destination, fee_source=0, note=""):
    """Convert between two treasury accounts of different currencies.

    Both amounts and the applied rate are kept. Each currency leg balances through its own FX clearing
    account; the realised difference against the reference rate (when known) is isolated on
    FX_DIFFERENCE-<destination>-REALIZED and excluded from the distributable result.
    """
    require_finance_access(actor, "approve")
    _engine_required()
    from .models import CurrencyConversion
    from apps.pricing.services import current_rate_to_usd
    existing = CurrencyConversion.objects.filter(client_key=client_key).first()
    if existing:
        return existing
    source = FinancialAccount.objects.select_for_update().get(pk=source_id)
    destination = FinancialAccount.objects.select_for_update().get(pk=destination_id)
    _treasury(source, source.currency_id)
    _treasury(destination, destination.currency_id)
    if source.currency_id == destination.currency_id:
        raise ValidationError("Utilisez un transfert interne pour deux comptes de même devise.")
    amount_source, amount_destination = _amount(amount_source), _amount(amount_destination)
    fee = Decimal(fee_source or 0).quantize(Decimal("0.01"))
    if fee < 0:
        raise ValidationError("Les frais de conversion ne peuvent pas être négatifs.")
    _validate_cash_payment(source, source.currency_id, amount_source + fee)
    rate = (amount_destination / amount_source).quantize(Decimal("0.000001"))
    reference = realized = None
    try:
        reference = (current_rate_to_usd(source.currency) / current_rate_to_usd(destination.currency)).quantize(Decimal("0.000001"))
        realized = (amount_destination - amount_source * reference).quantize(Decimal("0.01"))
    except ValidationError:  # no reference rate published: the difference stays unmeasured, not invented
        reference, realized = None, Decimal("0")
    conversion = CurrencyConversion.objects.create(client_key=client_key, source=source, destination=destination, amount_source=amount_source,
        amount_destination=amount_destination, rate_applied=rate, reference_rate=reference, fee_source=fee, realized_difference=realized or 0, note=note, created_by=actor)
    src_clearing = counterpart(source.currency, AccountType.FX_DIFFERENCE, category="CLEARING")
    dst_clearing = counterpart(destination.currency, AccountType.FX_DIFFERENCE, category="CLEARING")
    lines = [line(source, "CREDIT", amount_source + fee), line(src_clearing, "DEBIT", amount_source),
             line(destination, "DEBIT", amount_destination), line(dst_clearing, "CREDIT", amount_destination)]
    if fee:
        lines.append(line(counterpart(source.currency, AccountType.EXPENSE, category="FX_FEE"), "DEBIT", fee))
    if realized:
        # Move the realised gain/loss out of clearing so it stays visible and separate from commissions.
        realized_account = counterpart(destination.currency, AccountType.FX_DIFFERENCE, category="REALIZED")
        lines += [line(dst_clearing, "DEBIT" if realized > 0 else "CREDIT", abs(realized)), line(realized_account, "CREDIT" if realized > 0 else "DEBIT", abs(realized))]
    conversion.batch = record_event(actor=actor, source=conversion, event="FX_CONVERSION", effective_at=timezone.now(), lines=lines,
        description=f"Conversion {amount_source} {source.currency.code} → {amount_destination} {destination.currency.code} @ {rate}")
    conversion.save(update_fields=["batch"])
    _project_cash(source, "OUT", amount_source + fee, actor, f"Conversion #{conversion.pk}")
    _project_cash(destination, "IN", amount_destination, actor, f"Conversion #{conversion.pk}")
    audit_record(actor=actor, action="FX_CONVERSION", instance=conversion, after={"rate": str(rate), "realized": str(realized)})
    return conversion


@ledger_atomic
def record_revaluation(*, actor, client_key, account_id, difference, reason):
    """Unrealised revaluation of a treasury balance, kept apart from realised differences and from the result."""
    require_finance_access(actor, "approve")
    _engine_required()
    account = FinancialAccount.objects.select_for_update().get(pk=account_id)
    difference = Decimal(difference).quantize(Decimal("0.01"))
    if not difference or not reason.strip():
        raise ValidationError("Une réévaluation exige un montant non nul et un motif.")
    if account.legacy_cash_account_id or account.legacy_global_account_id:
        raise ValidationError("Une caisse projetée dans l’ancien registre ne peut pas être réévaluée ; réévaluez le compte de service ou numérique concerné.")
    existing = JournalBatch.objects.filter(idempotency_key=client_key).first()
    if existing:
        return existing
    from .services import _prepare_batch, _post_batch
    revaluation = counterpart(account.currency, AccountType.FX_DIFFERENCE, category="REVALUATION")
    lines = [line(account, "DEBIT" if difference > 0 else "CREDIT", abs(difference)), line(revaluation, "CREDIT" if difference > 0 else "DEBIT", abs(difference))]
    batch = _prepare_batch(actor=actor, idempotency_key=client_key, event_type="FX_REVALUATION", effective_at=timezone.now(), description=f"Réévaluation {account.code}: {reason.strip()}", lines=lines)
    return _post_batch(batch_id=batch.pk, actor=actor)


@ledger_atomic
def convert_commission_to_guarantee(*, actor, party, currency, amount, source):
    """Move commissions owed to the partner into their guarantee; no cash is received."""
    amount = _amount(amount)
    payable = counterpart(currency, AccountType.PARTNER_PAYABLE, party=party)
    if amount > ledger_balance(payable):
        raise ValidationError("La conversion dépasse les commissions dues au partenaire.")
    return record_event(actor=actor, source=source, event="COMMISSION_TO_GUARANTEE", effective_at=timezone.now(),
        lines=[line(payable, "DEBIT", amount), line(counterpart(currency, AccountType.PARTNER_GUARANTEE, party=party), "CREDIT", amount)])


# --------------------------------------------------------------------------- stakeholder requests

REQUEST_KINDS_BY_TYPE = {
    "SHAREHOLDER": {RequestKind.REINVEST_PROFIT, RequestKind.WITHDRAW_PROFIT},
    "INVESTOR": {RequestKind.INCREASE_INVESTMENT, RequestKind.WITHDRAW_INVESTMENT},
    "PARTNER": {RequestKind.INCREASE_GUARANTEE, RequestKind.CONVERT_COMMISSION},
}


def party_for(user):
    from apps.stakeholders.models import Stakeholder
    return Stakeholder.objects.filter(owner=user, is_active=True).first()


def distribution_remaining(distribution):
    executed = distribution.requests.filter(status=RequestStatus.EXECUTED)
    return distribution.amount - sum((row.amount for row in executed), Decimal("0"))


@ledger_atomic
def submit_request(*, actor, kind, amount, currency, note="", distribution_id=None, stakeholder_id=None):
    """A stakeholder (or an admin on their behalf) files a request; nothing is posted."""
    from apps.stakeholders.models import Stakeholder
    policy = finance_policy(actor)
    if policy.administer:
        if stakeholder_id is None:
            raise ValidationError("Indiquez la partie concernée.")
        party = Stakeholder.objects.get(pk=stakeholder_id)
    else:
        party = party_for(actor)
        if party is None or (stakeholder_id is not None and stakeholder_id != party.pk):
            raise PermissionDenied("Aucune partie prenante n’est liée à votre compte.")
    if kind not in REQUEST_KINDS_BY_TYPE.get(party.type, set()):
        raise ValidationError("Ce type de demande n’est pas disponible pour cette partie.")
    amount = _amount(amount)
    distribution = None
    if kind in {RequestKind.REINVEST_PROFIT, RequestKind.WITHDRAW_PROFIT}:
        from apps.profits.models import Distribution
        distribution = Distribution.objects.select_for_update().select_related("allocation__period").filter(pk=distribution_id, stakeholder=party, approval_batch__isnull=False).first()
        if distribution is None:
            raise ValidationError("Sélectionnez une distribution approuvée qui vous appartient.")
        if distribution.allocation.period.currency_id != currency.pk:
            raise ValidationError("La devise ne correspond pas à la distribution.")
        pending = distribution.requests.filter(status__in=[RequestStatus.SUBMITTED, RequestStatus.APPROVED])
        available = distribution_remaining(distribution) - sum((row.amount for row in pending), Decimal("0"))
        if amount > available:
            raise ValidationError(f"Le montant dépasse le bénéfice encore disponible ({available}).")
    elif kind == RequestKind.CONVERT_COMMISSION:
        if amount > ledger_balance(counterpart(currency, AccountType.PARTNER_PAYABLE, party=party)):
            raise ValidationError("Le montant dépasse les commissions dues.")
    elif kind == RequestKind.WITHDRAW_INVESTMENT:
        if amount > ledger_balance(counterpart(currency, AccountType.INVESTOR_FUNDS, party=party)):
            raise ValidationError("Le montant dépasse l’investissement en place.")
    request = StakeholderRequest.objects.create(stakeholder=party, kind=kind, amount=amount, currency=currency, note=note,
                                                distribution=distribution, submitted_by=actor)
    audit_record(actor=actor, action="REQUEST_SUBMIT", instance=request, after={"kind": kind, "amount": str(amount)})
    _notify_admins(f"Nouvelle demande {RequestKind(kind).label}", f"{party.name} · {amount} {currency.code}")
    return request


@ledger_atomic
def decide_request(*, actor, request_id, decision, comment=""):
    require_finance_access(actor, "approve")
    request = StakeholderRequest.objects.select_for_update().get(pk=request_id)
    if request.status != RequestStatus.SUBMITTED:
        raise ValidationError("Cette demande a déjà été traitée.")
    if decision not in {RequestStatus.APPROVED, RequestStatus.REJECTED}:
        raise ValidationError("Décision inconnue.")
    if decision == RequestStatus.REJECTED and not comment.strip():
        raise ValidationError("Un refus exige un commentaire.")
    request.status, request.decided_by, request.decided_at, request.decision_comment = decision, actor, timezone.now(), comment.strip()
    request.save(update_fields=["status", "decided_by", "decided_at", "decision_comment"])
    audit_record(actor=actor, action="REQUEST_DECIDE", instance=request, after={"decision": decision, "comment": comment})
    if request.stakeholder.owner_id:
        from apps.notifications.services import notify
        notify(recipient=request.stakeholder.owner, subject=f"Demande {request.get_status_display().lower()}", body=f"{request.get_kind_display()} · {request.amount} {request.currency.code}", level="INFO")
    return request


@ledger_atomic
def execute_request(*, actor, request_id, account_id=None, received_on=None):
    """Post the validated request exactly once. Cash kinds require a treasury account."""
    require_finance_access(actor, "approve")
    _engine_required()
    request = StakeholderRequest.objects.select_for_update().get(pk=request_id)
    if request.status == RequestStatus.EXECUTED:
        return request
    if request.status != RequestStatus.APPROVED:
        raise ValidationError("Seule une demande validée peut être exécutée.")
    party, currency, amount = request.stakeholder, request.currency, request.amount
    cash = None
    if request.kind in {RequestKind.WITHDRAW_PROFIT, RequestKind.INCREASE_INVESTMENT, RequestKind.WITHDRAW_INVESTMENT, RequestKind.INCREASE_GUARANTEE}:
        if account_id is None:
            raise ValidationError("Un compte de trésorerie est requis pour cette exécution.")
        cash = _treasury(FinancialAccount.objects.select_for_update().get(pk=account_id), currency.pk)
    if request.kind in {RequestKind.REINVEST_PROFIT, RequestKind.WITHDRAW_PROFIT}:
        if amount > distribution_remaining(request.distribution):
            raise ValidationError("Le bénéfice disponible ne couvre plus cette demande.")
        payable = counterpart(currency, AccountType.DISTRIBUTION_PAYABLE, party=party)
        if request.kind == RequestKind.REINVEST_PROFIT:
            lines = [line(payable, "DEBIT", amount), line(counterpart(currency, AccountType.CAPITAL, party=party), "CREDIT", amount)]
        else:
            _validate_cash_payment(cash, currency.pk, amount)
            lines = [line(payable, "DEBIT", amount), line(cash, "CREDIT", amount)]
    elif request.kind in {RequestKind.INCREASE_INVESTMENT, RequestKind.INCREASE_GUARANTEE}:
        origin = FundOrigin.INVESTOR if request.kind == RequestKind.INCREASE_INVESTMENT else FundOrigin.PARTNER_GUARANTEE
        contribution = record_contribution(actor=actor, client_key=f"request-{request.pk}", origin=origin, stakeholder_id=party.pk, amount=amount,
            currency=currency, received_on=received_on or timezone.localdate(), account_id=cash.pk, note=f"Demande #{request.pk}")
        request.execution_batch = contribution.batch
        lines = None
    elif request.kind == RequestKind.WITHDRAW_INVESTMENT:
        funds = counterpart(currency, AccountType.INVESTOR_FUNDS, party=party)
        if amount > ledger_balance(funds):
            raise ValidationError("Le retrait dépasse l’investissement en place.")
        _validate_cash_payment(cash, currency.pk, amount)
        lines = [line(funds, "DEBIT", amount), line(cash, "CREDIT", amount)]
    elif request.kind == RequestKind.CONVERT_COMMISSION:
        request.execution_batch = convert_commission_to_guarantee(actor=actor, party=party, currency=currency, amount=amount, source=request)
        lines = None
    else:
        raise ValidationError("Type de demande inconnu.")
    if lines is not None:
        request.execution_batch = record_event(actor=actor, source=request, event=f"REQUEST_{request.kind}", effective_at=timezone.now(), lines=lines)
        if cash is not None:
            _project_cash(cash, "OUT", amount, actor, f"Demande #{request.pk}")
    request.status, request.executed_at = RequestStatus.EXECUTED, timezone.now()
    request.save(update_fields=["status", "executed_at", "execution_batch"])
    if request.distribution_id:
        _sync_distribution(request.distribution, request.kind)
    audit_record(actor=actor, action="REQUEST_EXECUTE", instance=request, after={"batch": request.execution_batch_id})
    return request


def _sync_distribution(distribution, kind):
    from apps.profits.models import DistributionStatus
    if distribution_remaining(distribution) <= 0:
        kinds = set(distribution.requests.filter(status=RequestStatus.EXECUTED).values_list("kind", flat=True))
        distribution.status = DistributionStatus.REINVESTED if kinds == {RequestKind.REINVEST_PROFIT} else DistributionStatus.PAID
        distribution.paid_at = timezone.now()
        distribution.save(update_fields=["status", "paid_at"])


def _notify_admins(subject, body):
    from apps.accounts.models import Role, User
    from apps.notifications.services import notify
    for admin in User.objects.filter(role__in=[Role.ADMIN, Role.FINANCE_MANAGER], is_active=True):
        notify(recipient=admin, subject=subject, body=body, level="INFO")
