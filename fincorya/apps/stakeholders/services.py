from django.core.exceptions import ValidationError
from .models import PartnerOperation
from apps.finance.calculations import partner_commissions
from config.business_time import business_day_bounds


def _partner_rows(stakeholder, *, currency=None, start_date=None, end_date=None):
    rows = PartnerOperation.objects.filter(stakeholder=stakeholder, operation__status="COMPLETED")
    if start_date:
        rows = rows.filter(operation__created_at__gte=business_day_bounds(start_date, start_date)[0])
    if end_date:
        rows = rows.filter(operation__created_at__lt=business_day_bounds(end_date, end_date)[1])
    currencies = list(rows.values_list("operation__currency_id", flat=True).distinct()[:2])
    if currency:
        rows = rows.filter(operation__currency=currency)
    elif len(currencies) > 1:
        raise ValidationError("La devise est obligatoire lorsque les opérations du partenaire sont multidevises.")
    return rows.select_related("operation")


def partner_commission_base(stakeholder, *, currency=None, start_date=None, end_date=None):
    return partner_commissions(_partner_rows(stakeholder, currency=currency,
        start_date=start_date, end_date=end_date))["partner_gross"]


def fincorya_partner_share(stakeholder, *, currency=None, start_date=None, end_date=None):
    return partner_commissions(_partner_rows(stakeholder, currency=currency,
        start_date=start_date, end_date=end_date))["fincorya_share"]
