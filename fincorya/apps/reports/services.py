import csv
import calendar
from datetime import date
from io import BytesIO, StringIO
from decimal import Decimal
from pathlib import Path

from django.core.files.base import ContentFile
from django.conf import settings
from django.db.models import Prefetch
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.shapes import Drawing, String
from reportlab.platypus import CondPageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.accounts.models import Role
from apps.audit.services import record
from apps.contracts.models import Contract
from apps.operations.models import Operation
from apps.expenses.models import Expense
from apps.profits.models import Distribution, ProfitPeriod
from apps.stakeholders.models import Investment, PartnerOperation, PaymentFrequency, Stakeholder
from config.business_time import business_day_bounds
from .models import ReportExport


def _style_workbook(workbook):
    green = "006B4F"
    for sheet in workbook.worksheets:
        sheet.sheet_view.showGridLines = False
        sheet.freeze_panes = "A2"
        if sheet.max_row:
            for cell in sheet[1]:
                cell.fill = PatternFill("solid", fgColor=green)
                cell.font = Font(color="FFFFFF", bold=True)
                cell.alignment = Alignment(vertical="center", wrap_text=True)
            sheet.row_dimensions[1].height = 28
        for column in sheet.columns:
            letter = column[0].column_letter
            max_length = max((len(str(cell.value or "")) for cell in column), default=8)
            sheet.column_dimensions[letter].width = min(max(max_length + 2, 12), 34)
            for cell in column[1:]:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        sheet.auto_filter.ref = sheet.dimensions
    workbook.properties.creator = "FINCORYA Group"
    workbook.properties.title = "Rapport financier FINCORYA"
    workbook.properties.subject = "Rapport confidentiel"


def operation_report_snapshot(*, user, start_date, end_date):
    period_start, period_end = business_day_bounds(start_date, end_date)
    operations = Operation.objects.select_related("currency", "agent").filter(
        created_at__gte=period_start,
        created_at__lt=period_end,
    )
    if user.role == Role.AGENT:
        operations = operations.filter(agent=user)
    elif user.role != Role.ADMIN:
        operations = operations.none()
    rows, by_currency = [], {}
    for operation in operations.order_by("created_at", "id"):
        rows.append({
            "reference": str(operation.reference), "date": timezone.localtime(operation.created_at).strftime("%d/%m/%Y %H:%M"),
            "type": operation.get_type_display(), "status": operation.get_status_display(),
            "amount": str(operation.amount), "currency": operation.currency.code,
            "fee": str(operation.fee), "agent": operation.agent.get_full_name() or operation.agent.email,
            "agent_email": operation.agent.email,
            "service": operation.get_service_display(), "customer_identifier": operation.customer_identifier,
            "customer_name": operation.customer_name,
        })
        totals = by_currency.setdefault(operation.currency.code, {"amount": Decimal("0.00"), "fees": Decimal("0.00")})
        totals["amount"] += operation.amount
        totals["fees"] += operation.fee
    serialized_totals = {code: {"amount": str(values["amount"]), "fees": str(values["fees"])} for code, values in sorted(by_currency.items())}
    return {"start_date": str(start_date), "end_date": str(end_date), "rows": rows, "totals": {"count": len(rows), "by_currency": serialized_totals}}


def _csv_bytes(snapshot):
    stream = StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["reference", "date", "type", "service", "client", "identifiant", "status", "amount", "currency", "commission", "agent"])
    for row in snapshot["rows"]:
        writer.writerow([row[key] for key in ("reference", "date", "type", "service", "customer_name", "customer_identifier", "status", "amount", "currency", "fee", "agent")])
    for currency, totals in snapshot["totals"]["by_currency"].items():
        writer.writerow(["TOTAL", "", "", "", "", "", str(snapshot["totals"]["count"]) + " opérations", totals["amount"], currency, totals["fees"], ""])
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def _xlsx_bytes(snapshot):
    workbook = Workbook()
    detail = workbook.active
    detail.title = "Détail"
    detail.append(["Référence", "Date", "Type", "Service", "Client", "Identifiant", "Statut", "Montant", "Devise", "Commission", "Agent"])
    for row in snapshot["rows"]:
        detail.append([row["reference"], row["date"], row["type"], row["service"], row["customer_name"], row["customer_identifier"], row["status"], Decimal(row["amount"]), row["currency"], Decimal(row["fee"]), row["agent"]])
    summary = workbook.create_sheet("Synthèse")
    summary.append(["Indicateur", "Valeur"])
    summary.append(["Nombre d'opérations", snapshot["totals"]["count"]])
    for currency, totals in snapshot["totals"]["by_currency"].items():
        summary.append([f"Montant total {currency}", Decimal(totals["amount"])])
        summary.append([f"Frais totaux {currency}", Decimal(totals["fees"])])
    _style_workbook(workbook)
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def _report_fonts():
    candidates = []
    if settings.REPORT_FONT_DIR:
        font_dir = Path(settings.REPORT_FONT_DIR)
        candidates.append((font_dir / "arialn.ttf", font_dir / "arialnb.ttf"))
    candidates.extend([
        (settings.BASE_DIR / "static_src" / "fonts" / "arialn.ttf", settings.BASE_DIR / "static_src" / "fonts" / "arialnb.ttf"),
        (Path("C:/Windows/Fonts/arialn.ttf"), Path("C:/Windows/Fonts/arialnb.ttf")),
    ])
    for regular_path, bold_path in candidates:
        if regular_path.exists() and bold_path.exists():
            if "ArialNarrow" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("ArialNarrow", str(regular_path)))
                pdfmetrics.registerFont(TTFont("ArialNarrow-Bold", str(bold_path)))
            return "ArialNarrow", "ArialNarrow-Bold"
    return "Helvetica", "Helvetica-Bold"


def _summary_chart(chart_data, usable_width):
    if not chart_data:
        return None
    rows = list(chart_data.items())
    drawing = Drawing(usable_width, 52 * mm)
    chart = VerticalBarChart()
    chart.x, chart.y, chart.width, chart.height = 13 * mm, 10 * mm, usable_width - 62 * mm, 34 * mm
    keys = ("operations", "commissions", "expenses", "distributions")
    chart.data = [[float(values.get(key, 0)) for _, values in rows] for key in keys]
    chart.categoryAxis.categoryNames = [code for code, _ in rows]
    chart.categoryAxis.labels.fontSize = 8
    chart.valueAxis.labels.fontSize = 7
    for bar, color in zip(chart.bars, ("#006B4F", "#C9A227", "#9AA7A2", "#013B36")):
        bar.fillColor = colors.HexColor(color)
    drawing.add(chart)
    legend = Legend()
    legend.x, legend.y = usable_width - 45 * mm, 38 * mm
    legend.fontSize = 8
    legend.alignment = "right"
    legend.colorNamePairs = [
        (colors.HexColor("#006B4F"), "Operations"),
        (colors.HexColor("#C9A227"), "Commissions"),
        (colors.HexColor("#9AA7A2"), "Depenses"),
        (colors.HexColor("#013B36"), "Distributions"),
    ]
    drawing.add(legend)
    drawing.add(String(usable_width / 2, 1 * mm, "Operations, commissions, depenses et distributions par devise", textAnchor="middle", fontSize=8, fillColor=colors.HexColor("#596763")))
    return drawing


def _transparent_report_logo():
    logo_path = settings.BASE_DIR / "static_src" / "img" / "fincorya-report-logo.png"
    return logo_path if logo_path.exists() else None


def _build_branded_pdf(*, title, subtitle, sections, downloaded_by, landscape_mode=True, chart_data=None):
    stream = BytesIO()
    page_size = landscape(A4) if landscape_mode else A4
    document = SimpleDocTemplate(
        stream, pagesize=page_size, leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=27 * mm, bottomMargin=18 * mm, title=title, author="FINCORYA Group",
    )
    regular_font, bold_font = _report_fonts()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportTitle", parent=styles["Title"], fontName=bold_font, fontSize=22, leading=25, alignment=TA_CENTER, textColor=colors.HexColor("#013B36"), spaceAfter=3 * mm))
    styles.add(ParagraphStyle(name="ReportMeta", parent=styles["Normal"], fontName=regular_font, fontSize=14, leading=17, alignment=TA_JUSTIFY, textColor=colors.HexColor("#596763"), spaceAfter=6 * mm))
    styles.add(ParagraphStyle(name="SectionTitle", parent=styles["Heading2"], fontName=bold_font, fontSize=14, leading=17, textColor=colors.HexColor("#006B4F"), spaceBefore=5 * mm, spaceAfter=2.5 * mm))
    styles.add(ParagraphStyle(name="Cell", parent=styles["Normal"], fontName=regular_font, fontSize=9.5, leading=11, alignment=TA_JUSTIFY, textColor=colors.HexColor("#18201F")))
    styles.add(ParagraphStyle(name="CellCompact", parent=styles["Cell"], fontSize=7.5, leading=8.5))
    styles.add(ParagraphStyle(name="CellHeader", parent=styles["Cell"], fontName=bold_font, textColor=colors.white, alignment=TA_CENTER))
    styles.add(ParagraphStyle(name="CellHeaderCompact", parent=styles["CellCompact"], fontName=bold_font, textColor=colors.white, alignment=TA_CENTER))
    story = []
    story.extend([Paragraph(title, styles["ReportTitle"]), Paragraph(subtitle, styles["ReportMeta"])])
    usable_width = page_size[0] - document.leftMargin - document.rightMargin
    chart = _summary_chart(chart_data or {}, usable_width)
    if chart:
        story.extend([chart, Spacer(1, 2 * mm)])
    for section_number, (section_title, data) in enumerate(sections, start=1):
        story.append(CondPageBreak(34 * mm))
        story.append(Paragraph(f"{section_number}. {section_title}", styles["SectionTitle"]))
        if len(data) <= 1:
            story.append(Paragraph("Aucune donnee pour cette section.", styles["ReportMeta"]))
            continue
        rendered = []
        compact = len(data[0]) > 10
        for row_index, row in enumerate(data):
            if compact:
                style = styles["CellHeaderCompact"] if row_index == 0 else styles["CellCompact"]
            else:
                style = styles["CellHeader"] if row_index == 0 else styles["Cell"]
            rendered.append([Paragraph(str(value).replace("&", "&amp;").replace("<", "&lt;"), style) for value in row])
        widths = [usable_width / len(rendered[0])] * len(rendered[0])
        table = Table(rendered, colWidths=widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#006B4F")),
            ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#DCE3DF")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F8F5")]),
        ]))
        story.extend([table, Spacer(1, 3 * mm)])

    downloader = downloaded_by.get_full_name() or downloaded_by.email

    def header_footer(pdf_canvas, doc):
        pdf_canvas.saveState()
        pdf_canvas.setFont(bold_font, 9)
        pdf_canvas.setFillColor(colors.HexColor("#013B36"))
        page_logo = _transparent_report_logo()
        if page_logo:
            pdf_canvas.drawImage(
                str(page_logo), doc.leftMargin, page_size[1] - 23 * mm,
                width=30 * mm, height=20 * mm, preserveAspectRatio=True, mask="auto",
            )
        else:
            pdf_canvas.drawString(doc.leftMargin, page_size[1] - 12 * mm, "FINCORYA GROUP")
        pdf_canvas.drawRightString(page_size[0] - doc.rightMargin, page_size[1] - 12 * mm, title.upper())
        pdf_canvas.setStrokeColor(colors.HexColor("#C9A227"))
        pdf_canvas.line(doc.leftMargin, page_size[1] - 25 * mm, page_size[0] - doc.rightMargin, page_size[1] - 25 * mm)
        pdf_canvas.setStrokeColor(colors.HexColor("#DCE3DF")); pdf_canvas.line(doc.leftMargin, 10 * mm, page_size[0] - doc.rightMargin, 10 * mm)
        pdf_canvas.setFont(regular_font, 8); pdf_canvas.setFillColor(colors.HexColor("#596763"))
        pdf_canvas.drawString(doc.leftMargin, 6.5 * mm, "FINCORYA Group - Document confidentiel")
        pdf_canvas.drawCentredString(page_size[0] / 2, 6.5 * mm, f"Telecharge par : {downloader}")
        pdf_canvas.drawRightString(page_size[0] - doc.rightMargin, 6.5 * mm, f"Page {doc.page}")
        pdf_canvas.restoreState()

    document.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
    return stream.getvalue()


def _pdf_bytes(snapshot, user):
    rows = [["Date / heure", "Reference", "Type", "Service", "Client", "Identifiant", "Montant", "Commission", "Agent"]]
    for row in snapshot["rows"]:
        rows.append([row["date"], row["reference"].upper(), row["type"], row["service"], row["customer_name"] or "-", row["customer_identifier"] or "-", f"{row['amount']} {row['currency']}", f"{row['fee']} {row['currency']}", f"{row['agent']}\n{row['agent_email']}"])
    totals = [[code, values["amount"], values["fees"]] for code, values in snapshot["totals"]["by_currency"].items()]
    chart_data = {
        code: {"operations": values["amount"], "commissions": values["fees"], "expenses": 0, "distributions": 0}
        for code, values in snapshot["totals"]["by_currency"].items()
    }
    return _build_branded_pdf(
        title="Rapport des operations", subtitle=f"Periode du {snapshot['start_date']} au {snapshot['end_date']}",
        sections=[("Synthese", [["Devise", "Volume", "Commissions"], *totals]), ("Detail des operations", rows)], downloaded_by=user, landscape_mode=True, chart_data=chart_data if len(chart_data) > 1 else None,
    )


def generate_operation_report(*, user, start_date, end_date, format):
    format = format.upper()
    if format not in {"CSV", "XLSX", "PDF"}:
        raise ValueError("Format de rapport non pris en charge.")
    snapshot = operation_report_snapshot(user=user, start_date=start_date, end_date=end_date)
    export = ReportExport.objects.create(requested_by=user, kind="OPERATIONS", format=format, filters={"start_date": str(start_date), "end_date": str(end_date)}, snapshot=snapshot)
    try:
        content = _pdf_bytes(snapshot, user) if format == "PDF" else {"CSV": _csv_bytes, "XLSX": _xlsx_bytes}[format](snapshot)
        export.file.save(f"operations-{start_date}-{end_date}.{format.lower()}", ContentFile(content), save=False)
        export.status, export.completed_at = "READY", timezone.now()
        export.save(update_fields=["file", "status", "completed_at"])
    except Exception:
        if export.file:
            export.file.delete(save=False)
        export.status = "FAILED"
        export.completed_at = timezone.now()
        export.save(update_fields=["status", "completed_at"])
        raise
    record(actor=user, action="REPORT_GENERATE", instance=export, after={"format": format, "totals": snapshot["totals"]})
    return export


def monthly_financial_snapshot(*, user, year, month, agent=None, stakeholder=None, stakeholder_type=""):
    if user.role != Role.ADMIN:
        raise PermissionError("Seul l'administrateur peut générer le rapport mensuel consolidé.")
    start = date(int(year), int(month), 1)
    end = date(int(year), int(month), calendar.monthrange(int(year), int(month))[1])
    start_at, end_at = business_day_bounds(start, end)
    operations = Operation.objects.select_related("agent", "currency").filter(created_at__gte=start_at, created_at__lt=end_at)
    if agent:
        operations = operations.filter(agent=agent)
    operation_rows = [{
        "reference": str(op.reference), "date": timezone.localtime(op.created_at).strftime("%d/%m/%Y %H:%M"), "agent": op.agent.get_full_name() or op.agent.email,
        "service": op.get_service_display(), "type": op.get_type_display(), "client": op.customer_name,
        "identifier": op.customer_identifier, "amount": str(op.amount), "currency": op.currency.code,
        "commission": str(op.fee), "status": op.get_status_display(),
    } for op in operations.order_by("created_at", "id")]
    expenses = Expense.objects.select_related("currency").filter(incurred_on__range=(start, end), status="APPROVED")
    expense_rows = [{"date": str(row.incurred_on), "category": row.get_category_display(), "label": row.label, "amount": str(row.amount), "currency": row.currency.code} for row in expenses.exclude(category="SALARY")]
    salary_expenses = expenses.filter(category="SALARY").select_related("agent")
    if agent: salary_expenses = salary_expenses.filter(agent=agent)
    salary_rows = [{"agent": row.agent.get_full_name() or row.agent.email, "email": row.agent.email, "date": str(row.incurred_on), "amount": str(row.amount), "currency": row.currency.code} for row in salary_expenses if row.agent]
    periods = ProfitPeriod.objects.filter(start_date__lte=end, end_date__gte=start, status="FINALIZED")
    distributions = Distribution.objects.select_related("allocation__period__currency", "stakeholder").filter(allocation__period__in=periods)
    investments = Investment.objects.select_related("stakeholder", "currency").filter(invested_on__lte=end)
    partner_rows = PartnerOperation.objects.select_related("stakeholder", "operation__currency").filter(operation__created_at__gte=start_at, operation__created_at__lt=end_at)
    stakeholders = Stakeholder.objects.select_related("owner").prefetch_related(
        Prefetch("contracts", queryset=Contract.objects.order_by("-starts_on", "-id"), to_attr="ordered_contracts")
    ).filter(is_active=True)
    if stakeholder_type:
        stakeholders = stakeholders.filter(type=stakeholder_type)
        distributions = distributions.filter(stakeholder__type=stakeholder_type)
        investments = investments.filter(stakeholder__type=stakeholder_type)
        partner_rows = partner_rows.filter(stakeholder__type=stakeholder_type)
    if stakeholder:
        stakeholders = stakeholders.filter(pk=stakeholder.pk)
        distributions = distributions.filter(stakeholder=stakeholder)
        investments = investments.filter(stakeholder=stakeholder)
        partner_rows = partner_rows.filter(stakeholder=stakeholder)
    distribution_rows = [{"party": row.stakeholder.name, "type": row.stakeholder.get_type_display(), "amount": str(row.amount), "currency": row.allocation.period.currency.code, "status": row.get_status_display()} for row in distributions]
    frequency_divisor = {PaymentFrequency.MONTHLY: Decimal("12"), PaymentFrequency.QUARTERLY: Decimal("4"), PaymentFrequency.SEMIANNUAL: Decimal("2"), PaymentFrequency.ANNUAL: Decimal("1"), PaymentFrequency.AT_MATURITY: Decimal("1")}
    investment_rows = []
    for row in investments:
        annual_return = (row.amount * row.stakeholder.investor_return_percent / Decimal("100")).quantize(Decimal("0.01"))
        installment = (annual_return / frequency_divisor[row.stakeholder.payment_frequency]).quantize(Decimal("0.01"))
        investment_rows.append({"party": row.stakeholder.name, "identifier": str(row.stakeholder.public_id), "amount": str(row.amount), "currency": row.currency.code, "date": str(row.invested_on), "return_percent": str(row.stakeholder.investor_return_percent), "frequency": row.stakeholder.get_payment_frequency_display(), "annual_payment": str(annual_return), "payment_per_due_date": str(installment)})
    partner_commissions = [{"partner": row.stakeholder.name, "operation": str(row.operation.reference), "share_percent": str(row.share_percent), "share": str((row.operation.fee * row.share_percent / Decimal("100")).quantize(Decimal("0.01"))), "currency": row.operation.currency.code} for row in partner_rows]
    totals = {}
    for op in operation_rows:
        item = totals.setdefault(op["currency"], {"operations": Decimal("0"), "commissions": Decimal("0"), "expenses": Decimal("0"), "distributions": Decimal("0")})
        item["operations"] += Decimal(op["amount"]); item["commissions"] += Decimal(op["commission"])
    for row in expense_rows:
        totals.setdefault(row["currency"], {"operations": Decimal("0"), "commissions": Decimal("0"), "expenses": Decimal("0"), "distributions": Decimal("0")})["expenses"] += Decimal(row["amount"])
    for row in salary_rows:
        totals.setdefault(row["currency"], {"operations": Decimal("0"), "commissions": Decimal("0"), "expenses": Decimal("0"), "distributions": Decimal("0")})["expenses"] += Decimal(row["amount"])
    for row in distribution_rows:
        totals.setdefault(row["currency"], {"operations": Decimal("0"), "commissions": Decimal("0"), "expenses": Decimal("0"), "distributions": Decimal("0")})["distributions"] += Decimal(row["amount"])
    stakeholder_rows = []
    for party in stakeholders.order_by("type", "name"):
        contract = party.ordered_contracts[0] if party.ordered_contracts else None
        shareholder_value = (party.share_count * party.share_unit_value).quantize(Decimal("0.01"))
        estimated_dividend = (shareholder_value * party.dividend_percent / Decimal("100")).quantize(Decimal("0.01"))
        stakeholder_rows.append({
            "identifier": str(party.public_id), "type": party.get_type_display(), "name": party.name,
            "email": party.email, "phone": party.owner.phone if party.owner else "", "city": party.owner.city if party.owner else "",
            "contract": contract.title if contract else "", "starts_on": str(contract.starts_on) if contract else "",
            "ends_on": str(contract.ends_on) if contract and contract.ends_on else "Sans échéance",
            "clauses": contract.clauses if contract else "", "contract_status": contract.get_status_display() if contract else "",
            "return_percent": str(party.investor_return_percent) if party.type == "INVESTOR" else "-",
            "payment_frequency": party.get_payment_frequency_display() if party.type == "INVESTOR" else "-",
            "shares": str(party.share_count) if party.type == "SHAREHOLDER" else "-",
            "share_unit_value": str(party.share_unit_value) if party.type == "SHAREHOLDER" else "-",
            "dividend_percent": str(party.dividend_percent) if party.type == "SHAREHOLDER" else "-",
            "estimated_dividend": str(estimated_dividend) if party.type == "SHAREHOLDER" else "-",
            "partner_share_percent": str(party.partner_share_percent) if party.type == "PARTNER" else "-",
        })
    type_label = dict(Stakeholder._meta.get_field("type").choices).get(stakeholder_type, "Toutes")
    return {"period": {"start": str(start), "end": str(end)}, "filters": {"agent": agent.email if agent else "Tous", "stakeholder": stakeholder.name if stakeholder else "Tous", "stakeholder_type": type_label},
            "operations": operation_rows, "expenses": expense_rows, "salaries": salary_rows, "investments": investment_rows,
            "stakeholders": stakeholder_rows, "partner_commissions": partner_commissions, "distributions": distribution_rows,
            "totals": {code: {key: str(value) for key, value in values.items()} for code, values in totals.items()}}


def _monthly_csv(snapshot):
    stream = StringIO(newline="")
    writer = csv.writer(stream)
    for title, rows in (("PARTIES_PRENANTES", snapshot["stakeholders"]), ("OPERATIONS", snapshot["operations"]), ("DEPENSES", snapshot["expenses"]), ("SALAIRES", snapshot["salaries"]), ("INVESTISSEMENTS", snapshot["investments"]), ("COMMISSIONS_PARTENAIRES", snapshot["partner_commissions"]), ("DIVIDENDES_ET_PAIES", snapshot["distributions"])):
        writer.writerow([title])
        if rows:
            writer.writerow(rows[0].keys())
            for row in rows: writer.writerow(row.values())
        writer.writerow([])
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def _monthly_xlsx(snapshot):
    workbook = Workbook()
    workbook.remove(workbook.active)
    sections = (("Parties prenantes", snapshot["stakeholders"]), ("Opérations", snapshot["operations"]), ("Dépenses", snapshot["expenses"]), ("Salaires", snapshot["salaries"]), ("Investissements", snapshot["investments"]), ("Partenaires", snapshot["partner_commissions"]), ("Distributions", snapshot["distributions"]))
    for title, rows in sections:
        sheet = workbook.create_sheet(title)
        if rows:
            sheet.append(list(rows[0].keys()))
            for row in rows: sheet.append(list(row.values()))
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
    summary = workbook.create_sheet("Synthèse", 0)
    summary.append(["Période", snapshot["period"]["start"], snapshot["period"]["end"]])
    summary.append(["Agent", snapshot["filters"]["agent"]]); summary.append(["Catégorie", snapshot["filters"]["stakeholder_type"]]); summary.append(["Partie prenante", snapshot["filters"]["stakeholder"]])
    summary.append([]); summary.append(["Devise", "Opérations", "Commissions", "Dépenses", "Distributions"])
    for code, values in snapshot["totals"].items(): summary.append([code, *values.values()])
    _style_workbook(workbook)
    stream = BytesIO(); workbook.save(stream); return stream.getvalue()


def _monthly_pdf(snapshot, user):
    totals = [["Devise", "Operations", "Commissions", "Depenses", "Distributions"]]
    totals.extend([[code, values["operations"], values["commissions"], values["expenses"], values["distributions"]] for code, values in snapshot["totals"].items()])
    labels = {
        "identifier": "Identifiant", "type": "Type", "name": "Nom", "email": "E-mail", "phone": "Téléphone",
        "city": "Ville", "contract": "Contrat", "starts_on": "Début", "ends_on": "Fin", "clauses": "Clauses",
        "return_percent": "Rendement %", "payment_frequency": "Fréquence", "shares": "Parts",
        "share_unit_value": "Valeur unitaire", "dividend_percent": "Dividende %", "estimated_dividend": "Dividende estimé",
        "partner_share_percent": "Part partenaire %", "date": "Date", "reference": "Référence", "service": "Service",
        "client": "Client", "amount": "Montant", "currency": "Devise", "commission": "Commission", "agent": "Agent",
        "category": "Catégorie", "label": "Libellé", "party": "Partie prenante", "frequency": "Fréquence",
        "annual_payment": "Paiement annuel", "payment_per_due_date": "Paiement par échéance", "partner": "Partenaire",
        "operation": "Opération", "share_percent": "Part %", "share": "Commission", "status": "Statut",
    }
    definitions = [
        ("Fiches des parties prenantes", snapshot["stakeholders"], ["identifier", "type", "name", "email", "phone", "city", "contract", "starts_on", "ends_on", "clauses", "return_percent", "payment_frequency", "shares", "share_unit_value", "dividend_percent", "estimated_dividend", "partner_share_percent"]),
        ("Operations", snapshot["operations"], ["date", "reference", "type", "service", "client", "identifier", "amount", "currency", "commission", "agent"]),
        ("Depenses et charges", snapshot["expenses"], ["date", "category", "label", "amount", "currency"]),
        ("Salaires des agents", snapshot["salaries"], ["agent", "email", "date", "amount", "currency"]),
        ("Investissements et paiements calcules", snapshot["investments"], ["party", "identifier", "amount", "currency", "date", "return_percent", "frequency", "annual_payment", "payment_per_due_date"]),
        ("Commissions partenaires", snapshot["partner_commissions"], ["partner", "operation", "share_percent", "share", "currency"]),
        ("Dividendes et paiements", snapshot["distributions"], ["party", "type", "amount", "currency", "status"]),
    ]
    sections = [("Synthese par devise", totals)]
    for title, rows, keys in definitions:
        sections.append((title, [[labels.get(key, key.replace("_", " ").title()) for key in keys], *[[row.get(key, "-") for key in keys] for row in rows]]))
    subtitle = f"Periode du {snapshot['period']['start']} au {snapshot['period']['end']} | Categorie: {snapshot['filters']['stakeholder_type']} | Partie prenante: {snapshot['filters']['stakeholder']}"
    return _build_branded_pdf(title="Rapport financier mensuel", subtitle=subtitle, sections=sections, downloaded_by=user, landscape_mode=True, chart_data=snapshot["totals"])


def generate_monthly_report(*, user, year, month, format, agent=None, stakeholder=None, stakeholder_type=""):
    snapshot = monthly_financial_snapshot(
        user=user, year=year, month=month, agent=agent,
        stakeholder=stakeholder, stakeholder_type=stakeholder_type,
    )
    format = format.upper()
    builders = {"CSV": _monthly_csv, "XLSX": _monthly_xlsx, "PDF": _monthly_pdf}
    if format not in builders: raise ValueError("Format non pris en charge.")
    export = ReportExport.objects.create(requested_by=user, kind="MONTHLY_FINANCIAL", format=format, filters=snapshot["filters"] | snapshot["period"], snapshot=snapshot)
    try:
        content = _monthly_pdf(snapshot, user) if format == "PDF" else builders[format](snapshot)
        export.file.save(f"rapport-mensuel-{year}-{int(month):02d}.{format.lower()}", ContentFile(content), save=False)
        export.status = "READY"; export.completed_at = timezone.now(); export.save(update_fields=["file", "status", "completed_at"])
    except Exception:
        if export.file:
            export.file.delete(save=False)
        export.status = "FAILED"; export.completed_at = timezone.now(); export.save(update_fields=["status", "completed_at"])
        raise
    record(actor=user, action="MONTHLY_REPORT_GENERATE", instance=export, after={"period": snapshot["period"], "filters": snapshot["filters"]})
    return export
