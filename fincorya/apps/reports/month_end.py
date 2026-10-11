"""Declared month-end statement with an explicit comparison to posted records."""
from datetime import date
from decimal import Decimal

from django.db.models import Sum

from apps.expenses.models import Expense
from apps.finance.models import ImportRow
from apps.operations.models import Operation
from config.business_time import business_day_bounds


SOURCE_KEY = 'MONTH-END-2026-09-USER-STATEMENT'


def build_month_end_statement(*, year=2026, month=9):
    # The September URL remains compatible; other periods need their own
    # explicitly recorded statement, never a copy of September's figures.
    date(year, month, 1)
    source = ImportRow.objects.get(source_key=f"MONTH-END-{year:04d}-{month:02d}-USER-STATEMENT")
    data = source.payload
    if (data['year'], data['month']) != (year, month):
        raise ValueError('La déclaration ne correspond pas à la période demandée.')
    currency = data.get('currency', 'USD')
    rows, discrepancies = [], []
    for item in data['weeks']:
        start, end = business_day_bounds(date(year, month, item['start']), date(year, month, item['end']))
        ops = Operation.objects.filter(created_at__gte=start, created_at__lt=end,
                                       status='COMPLETED', currency__code=currency)
        own = ops.filter(partner_attribution__isnull=True).aggregate(v=Sum('fee'))['v'] or Decimal('0')
        partner = ops.filter(partner_attribution__stakeholder_id=data['partner_id']).aggregate(v=Sum('fee'))['v'] or Decimal('0')
        expenses = Expense.objects.filter(incurred_on__range=(date(year, month, item['start']),date(year, month, item['end'])),
                                           status='APPROVED', currency__code=currency).aggregate(v=Sum('amount'))['v'] or Decimal('0')
        row = dict(item, actual_commission=own, actual_partner=partner, actual_expense=expenses)
        rows.append(row)
        for label, expected, actual in [('Commissions FINCORYA',item['commission'],own),
                                         ('GO TRAVEL (Fantiny)',item['partner_commission'],partner),
                                         ('Charges',item['expense'],expenses)]:
            if Decimal(str(expected)) != actual:
                discrepancies.append({'week':item['number'],'label':label,'declared':expected,'actual':actual,'delta':actual-Decimal(str(expected))})
    totals = {key:sum(Decimal(str(r[key])) for r in rows) for key in ('commission','expense','partner_commission','actual_commission','actual_partner','actual_expense')}
    share = Decimal(data['fincorya_share_percent'])/100
    totals['fincorya_partner_share'] = (totals['partner_commission']*share).quantize(Decimal('.01'))
    totals['fantiny_share'] = totals['partner_commission']-totals['fincorya_partner_share']
    totals['declared_result'] = totals['commission']+totals['fincorya_partner_share']-totals['expense']
    return dict(source=source, statement=data, weeks=rows, totals=totals, discrepancies=discrepancies,
                cash_total=sum(Decimal(str(r['closing'])) for r in data['cash']),
                cash_opening=sum(Decimal(str(r['opening'])) for r in data['cash']))
