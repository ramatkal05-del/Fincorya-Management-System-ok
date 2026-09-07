from decimal import Decimal
from django.db.models import Sum
from django.core.exceptions import ValidationError
from .models import PartnerOperation


def partner_commission_base(stakeholder, *, currency=None, start_date=None, end_date=None):
    rows = PartnerOperation.objects.filter(stakeholder=stakeholder, operation__status="COMPLETED")
    if start_date:
        rows = rows.filter(operation__created_at__date__gte=start_date)
    if end_date:
        rows = rows.filter(operation__created_at__date__lte=end_date)
    currencies = list(rows.values_list("operation__currency_id", flat=True).distinct()[:2])
    if currency:
        rows = rows.filter(operation__currency=currency)
    elif len(currencies) > 1:
        raise ValidationError("La devise est obligatoire lorsque les opérations du partenaire sont multidevises.")
    return rows.aggregate(v=Sum("operation__fee"))["v"] or Decimal("0.00")


def fincorya_partner_share(stakeholder, *, currency=None, start_date=None, end_date=None):
    rate = stakeholder.partner_share_percent / Decimal("100")
    return (partner_commission_base(stakeholder, currency=currency, start_date=start_date, end_date=end_date) * rate).quantize(Decimal("0.01"))
