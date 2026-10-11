"""Pure monetary calculations shared by statements and closing results."""
from decimal import Decimal

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def partner_commissions(attributions):
    """Use the rate frozen on each operation, never today's partner agreement.

    Round each payable independently, as at journal posting time.
    The caller must scope the iterable to a single currency.
    """
    gross, partner = ZERO, ZERO
    for attribution in attributions:
        fee = attribution.operation.fee
        gross += fee
        partner += (fee * attribution.share_percent / Decimal("100")).quantize(CENT)
    return {"partner_gross": gross, "partner_share": partner, "fincorya_share": gross - partner}
