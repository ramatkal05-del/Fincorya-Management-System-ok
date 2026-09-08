"""
FINCORYA pricing services shared by application services.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .models import TariffSchedule, TariffTier


def lookup_fee(schedule: TariffSchedule, amount: Decimal) -> Decimal:
    """Return the tier where min <= amount <= max."""
    tier = (
        TariffTier.objects.filter(schedule=schedule, min_amount__lte=amount, max_amount__gte=amount)
        .order_by("min_amount")
        .first()
    )
    return tier.fixed_fee if tier else Decimal("0.00")


def resolve_fee(schedule: TariffSchedule, amount: Decimal, manual_fee: Decimal | None = None) -> Decimal:
    """
    FINCORYA rule: an agent may only reduce the automatic fee, never raise it.
    """
    auto_fee = lookup_fee(schedule, amount)
    if manual_fee is None:
        return auto_fee
    if manual_fee < 0:
        raise ValidationError(_("Les frais manuels ne peuvent pas être négatifs."))
    if manual_fee > auto_fee:
        raise ValidationError(
            _("Les frais saisis (%(manual)s) dépassent les frais calculés (%(auto)s). Seule une réduction est autorisée.")
            % {"manual": manual_fee, "auto": auto_fee}
        )
    return manual_fee


def current_rate_to_usd(currency) -> Decimal:
    # USD is the reference currency: it must never depend on a database row.
    if currency.code == "USD":
        return Decimal("1.000000")
    rate = currency.current_rate()
    if rate is None:
        raise ValidationError(_("Aucun taux de change n'est défini pour %(code)s.") % {"code": currency.code})
    return rate.rate_to_usd


def to_usd(amount: Decimal, currency) -> Decimal:
    return (amount * current_rate_to_usd(currency)).quantize(Decimal("0.01"))


def from_usd(amount_usd: Decimal, currency) -> Decimal:
    rate = current_rate_to_usd(currency)
    if rate <= 0:
        raise ValidationError(_("Le taux de change doit être supérieur à zéro."))
    return (amount_usd / rate).quantize(Decimal("0.01"))
