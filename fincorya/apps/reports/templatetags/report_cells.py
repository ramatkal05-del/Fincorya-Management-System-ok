from datetime import date, datetime
from decimal import Decimal

from django import template
from django.utils import formats

register = template.Library()


@register.filter
def report_cell(value):
    """Uniform rendering of report cells: money with two decimals, dates in French format."""
    if isinstance(value, Decimal):
        return formats.number_format(value.quantize(Decimal("0.01")), decimal_pos=2, use_l10n=True, force_grouping=True)
    if isinstance(value, datetime):
        return formats.date_format(value, "d/m/Y H:i")
    if isinstance(value, date):
        return formats.date_format(value, "d/m/Y")
    return "" if value is None else value
