import os
import sys
from decimal import Decimal
from io import BytesIO
from pathlib import Path

# Django setup
sys.path.insert(0, r"c:\Users\User\Downloads\fincorya\fincorya")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from django.conf import settings
from apps.accounts.models import User
from apps.reports.services import monthly_financial_snapshot, _report_fonts, _transparent_report_logo

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, Image
)

# Colors
PRIMARY_GREEN = colors.HexColor("#006B4F")
DARK_GREEN = colors.HexColor("#013B36")
GOLD = colors.HexColor("#C9A227")
LIGHT_BG = colors.HexColor("#F7F8F5")
TEXT_COLOR = colors.HexColor("#18201F")
MUTED_TEXT = colors.HexColor("#596763")
BORDER_COLOR = colors.HexColor("#DCE3DF")
BORDER_DARK = colors.HexColor("#A8B5B0")
WHITE = colors.HexColor("#FFFFFF")
ROW_ALT = colors.HexColor("#F9FAF8")

def fmt_money(val, currency=None):
    if val is None or str(val).strip() in ("", "-"):
        return "-"
    if str(val).strip().upper() == "N/A":
        return "N/A"
    try:
        d = Decimal(str(val))
        s = f"{d:,.2f}".replace(",", " ").replace(".", ",")
        return f"{s} {currency}" if currency else s
    except Exception:
        return str(val)

def fmt_percent(val):
    if val is None or str(val).strip() in ("", "-"):
        return "-"
    if str(val).strip().upper() == "N/A":
        return "N/A"
    try:
        d = Decimal(str(val))
        s = f"{d:,.2f}".replace(",", " ").replace(".", ",")
        return f"{s} %"
    except Exception:
        return f"{val} %"

def fmt_date(val):
    if not val or str(val).strip() in ("", "-"):
        return "-"
    s = str(val).strip()
    if s == "Sans échéance":
        return s
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        parts = s.split("-")
        return f"{parts[2]}/{parts[1]}/{parts[0]}"
    return s

def escape_xml(text):
    if text is None:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class ProfessionalNumberedCanvas(canvas.Canvas):
    """Canvas that computes total pages and draws headers & footers dynamically."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []
        self.doc_title = kwargs.get("doc_title", "RAPPORT FINANCIER MENSUEL")
        self.downloader = kwargs.get("downloader", "FINCORYA Group")
        self.logo_path = kwargs.get("logo_path", None)

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_elements(num_pages)
            super().showPage()
        super().save()

    def draw_page_elements(self, page_count):
        self.saveState()
        page_w, page_h = self._pagesize
        left_m = 12 * mm
        right_m = page_w - 12 * mm
        regular_font, bold_font = _report_fonts()

        # Header (Pages >= 2)
        if self._pageNumber > 1:
            self.setFont(bold_font, 8)
            self.setFillColor(DARK_GREEN)
            if self.logo_path and os.path.exists(self.logo_path):
                self.drawImage(
                    str(self.logo_path), left_m, page_h - 15 * mm,
                    width=26 * mm, height=9 * mm, preserveAspectRatio=True, mask="auto"
                )
            else:
                self.drawString(left_m, page_h - 12 * mm, "FINCORYA GROUP")

            self.drawRightString(right_m, page_h - 12 * mm, "RAPPORT FINANCIER MENSUEL")

            # Gold separator line
            self.setStrokeColor(GOLD)
            self.setLineWidth(0.75)
            self.line(left_m, page_h - 17 * mm, right_m, page_h - 17 * mm)

        # Footer (All pages)
        self.setStrokeColor(BORDER_COLOR)
        self.setLineWidth(0.5)
        self.line(left_m, 11 * mm, right_m, 11 * mm)

        self.setFont(regular_font, 7.5)
        self.setFillColor(MUTED_TEXT)
        self.drawString(left_m, 6.5 * mm, "FINCORYA Group  —  Document confidentiel")
        self.drawCentredString(page_w / 2, 6.5 * mm, f"Téléchargé par : {self.downloader}")
        self.drawRightString(right_m, 6.5 * mm, f"Page {self._pageNumber} sur {page_count}")

        self.restoreState()


def build_monthly_financial_pdf(snapshot, user):
    """
    Builds the complete, executive-grade monthly financial PDF report according
    to the FINCORYA design specification and brand guidelines.
    """
    stream = BytesIO()
    regular_font, bold_font = _report_fonts()
    page_w, page_h = landscape(A4)
    left_m = 12 * mm
    right_m = 12 * mm
    top_m = 19 * mm
    bottom_m = 15 * mm
    usable_width = page_w - left_m - right_m  # approx 773.8 pt (273 mm)

    doc = SimpleDocTemplate(
        stream,
        pagesize=landscape(A4),
        leftMargin=left_m,
        rightMargin=right_m,
        topMargin=top_m,
        bottomMargin=bottom_m,
        title="Rapport financier mensuel FINCORYA",
        author="FINCORYA Group",
    )

    downloader_name = user.get_full_name() or user.email if user else "Direction Générale"
    logo_path = _transparent_report_logo()

    # Styles
    styles = {
        "Title": ParagraphStyle(
            "DocTitle", fontName=bold_font, fontSize=18, leading=21,
            textColor=DARK_GREEN, alignment=TA_LEFT
        ),
        "FilterText": ParagraphStyle(
            "DocFilter", fontName=regular_font, fontSize=8.5, leading=11,
            textColor=TEXT_COLOR, alignment=TA_LEFT
        ),
        "SectionHeading": ParagraphStyle(
            "SectionH", fontName=bold_font, fontSize=11, leading=14,
            textColor=PRIMARY_GREEN, spaceBefore=3.5 * mm, spaceAfter=1.8 * mm,
            keepWithNext=True
        ),
        "SubSectionHeading": ParagraphStyle(
            "SubSectionH", fontName=bold_font, fontSize=9, leading=11.5,
            textColor=DARK_GREEN, spaceBefore=2 * mm, spaceAfter=1.2 * mm,
            keepWithNext=True
        ),
        "KpiLabel": ParagraphStyle(
            "KpiL", fontName=bold_font, fontSize=7, leading=8.5,
            textColor=PRIMARY_GREEN, alignment=TA_CENTER
        ),
        "KpiValue": ParagraphStyle(
            "KpiV", fontName=bold_font, fontSize=11.5, leading=13.5,
            textColor=DARK_GREEN, alignment=TA_CENTER
        ),
        "KpiSub": ParagraphStyle(
            "KpiS", fontName=regular_font, fontSize=6.5, leading=8,
            textColor=MUTED_TEXT, alignment=TA_CENTER
        ),
        "Th": ParagraphStyle(
            "TableHead", fontName=bold_font, fontSize=7.5, leading=9,
            textColor=WHITE, alignment=TA_LEFT
        ),
        "ThRight": ParagraphStyle(
            "TableHeadR", fontName=bold_font, fontSize=7.5, leading=9,
            textColor=WHITE, alignment=TA_RIGHT
        ),
        "ThCenter": ParagraphStyle(
            "TableHeadC", fontName=bold_font, fontSize=7.5, leading=9,
            textColor=WHITE, alignment=TA_CENTER
        ),
        "Td": ParagraphStyle(
            "TableData", fontName=regular_font, fontSize=7, leading=8.5,
            textColor=TEXT_COLOR, alignment=TA_LEFT
        ),
        "TdBold": ParagraphStyle(
            "TableDataB", fontName=bold_font, fontSize=7, leading=8.5,
            textColor=TEXT_COLOR, alignment=TA_LEFT
        ),
        "TdRight": ParagraphStyle(
            "TableDataR", fontName=regular_font, fontSize=7, leading=8.5,
            textColor=TEXT_COLOR, alignment=TA_RIGHT
        ),
        "TdCenter": ParagraphStyle(
            "TableDataC", fontName=regular_font, fontSize=7, leading=8.5,
            textColor=TEXT_COLOR, alignment=TA_CENTER
        ),
        "TotalLabel": ParagraphStyle(
            "TotLabel", fontName=bold_font, fontSize=7.5, leading=9,
            textColor=WHITE, alignment=TA_LEFT
        ),
        "TotalValue": ParagraphStyle(
            "TotValue", fontName=bold_font, fontSize=7.5, leading=9,
            textColor=WHITE, alignment=TA_RIGHT
        ),
        "TotalCenter": ParagraphStyle(
            "TotCenter", fontName=bold_font, fontSize=7.5, leading=9,
            textColor=WHITE, alignment=TA_CENTER
        ),
        "CardHead": ParagraphStyle(
            "CardH", fontName=bold_font, fontSize=8, leading=10,
            textColor=WHITE, alignment=TA_LEFT
        ),
        "CardHeadRight": ParagraphStyle(
            "CardHR", fontName=bold_font, fontSize=7.5, leading=9.5,
            textColor=GOLD, alignment=TA_RIGHT
        ),
        "CardFieldValue": ParagraphStyle(
            "CardFV", fontName=regular_font, fontSize=7, leading=8.5,
            textColor=TEXT_COLOR, alignment=TA_LEFT
        ),
        "NoticeEmpty": ParagraphStyle(
            "NoticeE", fontName=regular_font, fontSize=7.5, leading=9.5,
            textColor=MUTED_TEXT, alignment=TA_LEFT
        ),
    }

    story = []

    # =========================================================================
    # EN-TÊTE DE LA PREMIÈRE PAGE (Logo + Titre + Période + Filtres)
    # =========================================================================
    start_date_fr = fmt_date(snapshot["period"]["start"])
    end_date_fr = fmt_date(snapshot["period"]["end"])
    filter_agent = escape_xml(snapshot["filters"].get("agent", "Tous"))
    filter_stakeholder = escape_xml(snapshot["filters"].get("stakeholder", "Tous"))
    filter_category = escape_xml(snapshot["filters"].get("stakeholder_type", "Toutes"))

    logo_element = ""
    if logo_path and os.path.exists(logo_path):
        logo_element = Image(str(logo_path), width=35 * mm, height=14 * mm)
    else:
        logo_element = Paragraph("<b>FINCORYA GROUP</b>", styles["Title"])

    header_text_cells = [
        Paragraph("RAPPORT FINANCIER MENSUEL", styles["Title"]),
        Spacer(1, 0.8 * mm),
        Paragraph(
            f"<b>Période d'activité :</b> du {start_date_fr} au {end_date_fr}  &nbsp;|&nbsp;  "
            f"<b>Catégorie :</b> {filter_category}  &nbsp;|&nbsp;  "
            f"<b>Partie prenante :</b> {filter_stakeholder}  &nbsp;|&nbsp;  "
            f"<b>Agent :</b> {filter_agent}",
            styles["FilterText"]
        ),
    ]

    header_table = Table(
        [[logo_element, header_text_cells]],
        colWidths=[42 * mm, usable_width - 42 * mm],
        hAlign="LEFT"
    )
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 1 * mm))
    story.append(HRFlowable(width="100%", thickness=1, color=GOLD, spaceBefore=0.5, spaceAfter=2.5 * mm))

    # =========================================================================
    # BLOCS CHIFFRÉS SOBRES (KPIs)
    # =========================================================================
    tot_ops = sum(Decimal(v["operations"]) for v in snapshot["totals"].values())
    tot_comm = sum(Decimal(v["commissions"]) for v in snapshot["totals"].values())
    tot_exp = sum(Decimal(v["expenses"]) for v in snapshot["totals"].values())
    tot_dist = sum(Decimal(v["distributions"]) for v in snapshot["totals"].values())
    tot_contrib = sum(Decimal(v.get("contributions", "0")) for v in snapshot["totals"].values())
    curr_label = list(snapshot["totals"].keys())[0] if len(snapshot["totals"]) == 1 else "USD"
    nb_ops = len(snapshot.get("operations", []))

    kpi_cards = [
        [
            Paragraph("VOLUME DES OPÉRATIONS", styles["KpiLabel"]),
            Paragraph(f"{fmt_money(tot_ops)} <font size=7.5>{curr_label}</font>", styles["KpiValue"]),
            Paragraph(f"{nb_ops} opérations traitées", styles["KpiSub"]),
        ],
        [
            Paragraph("COMMISSIONS NETTES", styles["KpiLabel"]),
            Paragraph(f"{fmt_money(tot_comm)} <font size=7.5>{curr_label}</font>", styles["KpiValue"]),
            Paragraph("Marge brute de services", styles["KpiSub"]),
        ],
        [
            Paragraph("DÉPENSES & CHARGES", styles["KpiLabel"]),
            Paragraph(f"{fmt_money(tot_exp)} <font size=7.5>{curr_label}</font>", styles["KpiValue"]),
            Paragraph(f"{len(snapshot.get('expenses', []))} charges approuvées", styles["KpiSub"]),
        ],
        [
            Paragraph("DISTRIBUTIONS EFFECTUÉES", styles["KpiLabel"]),
            Paragraph(f"{fmt_money(tot_dist)} <font size=7.5>{curr_label}</font>", styles["KpiValue"]),
            Paragraph(f"{len(snapshot.get('distributions', []))} allocations", styles["KpiSub"]),
        ],
        [
            Paragraph("APPORTS EN CAPITAL", styles["KpiLabel"]),
            Paragraph(f"{fmt_money(tot_contrib)} <font size=7.5>{curr_label}</font>", styles["KpiValue"]),
            Paragraph(f"{len(snapshot.get('contributions', []))} versements reçus", styles["KpiSub"]),
        ],
    ]

    card_w = usable_width / 5.0
    kpi_table = Table([kpi_cards], colWidths=[card_w] * 5, hAlign="LEFT")
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ("LINEABOVE", (0, 0), (-1, 0), 2, PRIMARY_GREEN),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 2.5 * mm))

    # =========================================================================
    # 1. SYNTHÈSE FINANCIÈRE PAR DEVISE (Exact width matching usable_width)
    # =========================================================================
    story.append(Paragraph("1. Synthèse financière par devise", styles["SectionHeading"]))

    synth_headers = [
        Paragraph("Devise", styles["ThCenter"]),
        Paragraph("Volume des opérations", styles["ThRight"]),
        Paragraph("Commissions nettes", styles["ThRight"]),
        Paragraph("Dépenses & charges", styles["ThRight"]),
        Paragraph("Distributions", styles["ThRight"]),
        Paragraph("Apports reçus", styles["ThRight"]),
    ]
    synth_data = [synth_headers]

    for curr_code, vals in sorted(snapshot["totals"].items()):
        synth_data.append([
            Paragraph(curr_code, styles["TdCenter"]),
            Paragraph(fmt_money(vals["operations"]), styles["TdRight"]),
            Paragraph(fmt_money(vals["commissions"]), styles["TdRight"]),
            Paragraph(fmt_money(vals["expenses"]), styles["TdRight"]),
            Paragraph(fmt_money(vals["distributions"]), styles["TdRight"]),
            Paragraph(fmt_money(vals.get("contributions", "0")), styles["TdRight"]),
        ])

    # Grand Total row
    synth_data.append([
        Paragraph("TOTAL CONSOLIDÉ", styles["TotalLabel"]),
        Paragraph(fmt_money(tot_ops), styles["TotalValue"]),
        Paragraph(fmt_money(tot_comm), styles["TotalValue"]),
        Paragraph(fmt_money(tot_exp), styles["TotalValue"]),
        Paragraph(fmt_money(tot_dist), styles["TotalValue"]),
        Paragraph(fmt_money(tot_contrib), styles["TotalValue"]),
    ])

    col_dev = 54
    col_other = (usable_width - col_dev) / 5.0
    synth_widths = [col_dev, col_other, col_other, col_other, col_other, col_other]
    synth_table = Table(synth_data, colWidths=synth_widths, repeatRows=1, hAlign="LEFT")
    synth_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY_GREEN),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -2), 0.5, BORDER_COLOR),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [WHITE, ROW_ALT]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("BACKGROUND", (0, -1), (-1, -1), DARK_GREEN),
        ("LINEABOVE", (0, -1), (-1, -1), 1, GOLD),
    ]))
    story.append(synth_table)
    story.append(Spacer(1, 3 * mm))

    # =========================================================================
    # 2. DÉPENSES ET CHARGES
    # =========================================================================
    story.append(Paragraph("2. Dépenses et charges", styles["SectionHeading"]))
    expenses = snapshot.get("expenses", [])

    if expenses:
        exp_headers = [
            Paragraph("Date", styles["ThCenter"]),
            Paragraph("Catégorie", styles["Th"]),
            Paragraph("Libellé / Justificatif", styles["Th"]),
            Paragraph("Montant", styles["ThRight"]),
            Paragraph("Devise", styles["ThCenter"]),
        ]
        exp_data = [exp_headers]
        tot_exp_cat = Decimal("0")
        exp_curr = "USD"
        for r in expenses:
            amt = Decimal(r["amount"])
            tot_exp_cat += amt
            exp_curr = r["currency"]
            exp_data.append([
                Paragraph(fmt_date(r["date"]), styles["TdCenter"]),
                Paragraph(escape_xml(r["category"]), styles["TdBold"]),
                Paragraph(escape_xml(r["label"]), styles["Td"]),
                Paragraph(fmt_money(amt), styles["TdRight"]),
                Paragraph(r["currency"], styles["TdCenter"]),
            ])
        exp_data.append([
            Paragraph("TOTAL DÉPENSES & CHARGES", styles["TotalLabel"]),
            Paragraph("", styles["TotalLabel"]),
            Paragraph(f"{len(expenses)} enregistrements validés", styles["TotalLabel"]),
            Paragraph(fmt_money(tot_exp_cat), styles["TotalValue"]),
            Paragraph(exp_curr, styles["TotalCenter"]),
        ])
        exp_widths = [65, 125, usable_width - 65 - 125 - 85 - 45, 85, 45]
        exp_table = Table(exp_data, colWidths=exp_widths, repeatRows=1, hAlign="LEFT")
        exp_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PRIMARY_GREEN),
            ("GRID", (0, 0), (-1, -2), 0.5, BORDER_COLOR),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [WHITE, ROW_ALT]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("BACKGROUND", (0, -1), (-1, -1), DARK_GREEN),
            ("LINEABOVE", (0, -1), (-1, -1), 1, GOLD),
            ("SPAN", (0, -1), (1, -1)),
        ]))
        story.append(exp_table)
    else:
        empty_box = Table(
            [[Paragraph("<i>Aucune dépense ni charge enregistrée pour cette période.</i>", styles["NoticeEmpty"])]],
            colWidths=[usable_width], hAlign="LEFT"
        )
        empty_box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
            ("BOX", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("PADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(empty_box)

    story.append(Spacer(1, 3 * mm))

    # =========================================================================
    # 3. SALAIRES DES AGENTS
    # =========================================================================
    story.append(Paragraph("3. Salaires des agents", styles["SectionHeading"]))
    salaries = snapshot.get("salaries", [])

    if salaries:
        sal_headers = [
            Paragraph("Agent", styles["Th"]),
            Paragraph("E-mail", styles["Th"]),
            Paragraph("Date", styles["ThCenter"]),
            Paragraph("Montant", styles["ThRight"]),
            Paragraph("Devise", styles["ThCenter"]),
        ]
        sal_data = [sal_headers]
        tot_sal = Decimal("0")
        sal_curr = "USD"
        for r in salaries:
            amt = Decimal(r["amount"])
            tot_sal += amt
            sal_curr = r["currency"]
            sal_data.append([
                Paragraph(escape_xml(r["agent"]), styles["TdBold"]),
                Paragraph(escape_xml(r["email"]), styles["Td"]),
                Paragraph(fmt_date(r["date"]), styles["TdCenter"]),
                Paragraph(fmt_money(amt), styles["TdRight"]),
                Paragraph(r["currency"], styles["TdCenter"]),
            ])
        sal_data.append([
            Paragraph("TOTAL SALAIRES", styles["TotalLabel"]),
            Paragraph(f"{len(salaries)} versements", styles["TotalLabel"]),
            Paragraph("", styles["TotalLabel"]),
            Paragraph(fmt_money(tot_sal), styles["TotalValue"]),
            Paragraph(sal_curr, styles["TotalCenter"]),
        ])
        sal_widths = [140, 180, 80, 85, usable_width - 140 - 180 - 80 - 85]
        sal_table = Table(sal_data, colWidths=sal_widths, repeatRows=1, hAlign="LEFT")
        sal_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PRIMARY_GREEN),
            ("GRID", (0, 0), (-1, -2), 0.5, BORDER_COLOR),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [WHITE, ROW_ALT]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("BACKGROUND", (0, -1), (-1, -1), DARK_GREEN),
            ("LINEABOVE", (0, -1), (-1, -1), 1, GOLD),
        ]))
        story.append(sal_table)
    else:
        empty_box = Table(
            [[Paragraph("<i>Aucun salaire d'agent enregistré sur cette période.</i>", styles["NoticeEmpty"])]],
            colWidths=[usable_width], hAlign="LEFT"
        )
        empty_box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
            ("BOX", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("PADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(empty_box)

    story.append(Spacer(1, 3 * mm))

    # =========================================================================
    # 4. INVESTISSEMENTS ET PAIEMENTS CALCULÉS (& APPORTS REÇUS)
    # =========================================================================
    story.append(PageBreak())  # Clean break to start Investments on fresh page
    story.append(Paragraph("4. Investissements et paiements calculés", styles["SectionHeading"]))
    investments = snapshot.get("investments", [])
    contributions = snapshot.get("contributions", [])

    uuid_map = {}
    for party in snapshot.get("stakeholders", []):
        uid = party["identifier"]
        uuid_map[uid] = f"[{uid[:8]}]"

    if investments:
        story.append(Paragraph("4.1. Investissements contractuels", styles["SubSectionHeading"]))
        inv_headers = [
            Paragraph("Partie prenante", styles["Th"]),
            Paragraph("Réf.", styles["ThCenter"]),
            Paragraph("Date", styles["ThCenter"]),
            Paragraph("Montant investi", styles["ThRight"]),
            Paragraph("Devise", styles["ThCenter"]),
            Paragraph("Rendement", styles["ThRight"]),
            Paragraph("Fréquence", styles["ThCenter"]),
            Paragraph("Paiement annuel", styles["ThRight"]),
            Paragraph("Paiement / échéance", styles["ThRight"]),
        ]
        inv_data = [inv_headers]
        tot_inv = Decimal("0")
        tot_ann = Decimal("0")
        tot_inst = Decimal("0")
        inv_curr = "USD"
        for r in investments:
            tot_inv += Decimal(r["amount"])
            tot_ann += Decimal(r["annual_payment"])
            tot_inst += Decimal(r["payment_per_due_date"])
            inv_curr = r["currency"]
            uid_short = uuid_map.get(r["identifier"], f"[{r['identifier'][:8]}]")
            inv_data.append([
                Paragraph(escape_xml(r["party"]), styles["TdBold"]),
                Paragraph(uid_short, styles["TdCenter"]),
                Paragraph(fmt_date(r["date"]), styles["TdCenter"]),
                Paragraph(fmt_money(r["amount"]), styles["TdRight"]),
                Paragraph(r["currency"], styles["TdCenter"]),
                Paragraph(fmt_percent(r["return_percent"]), styles["TdRight"]),
                Paragraph(escape_xml(r["frequency"]), styles["TdCenter"]),
                Paragraph(fmt_money(r["annual_payment"]), styles["TdRight"]),
                Paragraph(fmt_money(r["payment_per_due_date"]), styles["TdRight"]),
            ])
        inv_data.append([
            Paragraph("TOTAL INVESTISSEMENTS", styles["TotalLabel"]),
            Paragraph("", styles["TotalLabel"]),
            Paragraph("", styles["TotalLabel"]),
            Paragraph(fmt_money(tot_inv), styles["TotalValue"]),
            Paragraph(inv_curr, styles["TotalCenter"]),
            Paragraph("-", styles["TotalCenter"]),
            Paragraph("-", styles["TotalCenter"]),
            Paragraph(fmt_money(tot_ann), styles["TotalValue"]),
            Paragraph(fmt_money(tot_inst), styles["TotalValue"]),
        ])
        inv_widths = [135, 60, 60, 75, 40, 65, 80, 85, 85]
        scale = usable_width / sum(inv_widths)
        inv_widths = [w * scale for w in inv_widths]
        inv_table = Table(inv_data, colWidths=inv_widths, repeatRows=1, hAlign="LEFT")
        inv_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PRIMARY_GREEN),
            ("GRID", (0, 0), (-1, -2), 0.5, BORDER_COLOR),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [WHITE, ROW_ALT]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("BACKGROUND", (0, -1), (-1, -1), DARK_GREEN),
            ("LINEABOVE", (0, -1), (-1, -1), 1, GOLD),
            ("SPAN", (0, -1), (2, -1)),
        ]))
        story.append(inv_table)
        story.append(Spacer(1, 2 * mm))

    # Sub-table 4.2 Apports reçus
    if contributions:
        story.append(Paragraph("4.2. Apports reçus en capital et garanties", styles["SubSectionHeading"]))
        contrib_headers = [
            Paragraph("Date", styles["ThCenter"]),
            Paragraph("Origine de l'apport", styles["Th"]),
            Paragraph("Partie prenante", styles["Th"]),
            Paragraph("Compte de réception", styles["Th"]),
            Paragraph("Référence externe", styles["Th"]),
            Paragraph("Montant", styles["ThRight"]),
            Paragraph("Devise", styles["ThCenter"]),
        ]
        contrib_data = [contrib_headers]
        tot_contrib_sec = Decimal("0")
        contrib_curr = "USD"
        for r in contributions:
            amt = Decimal(r["amount"])
            tot_contrib_sec += amt
            contrib_curr = r["currency"]
            contrib_data.append([
                Paragraph(fmt_date(r["date"]), styles["TdCenter"]),
                Paragraph(escape_xml(r["origin"]), styles["TdBold"]),
                Paragraph(escape_xml(r["party"]), styles["Td"]),
                Paragraph(escape_xml(r["account"]), styles["Td"]),
                Paragraph(escape_xml(r.get("reference", "-")), styles["Td"]),
                Paragraph(fmt_money(amt), styles["TdRight"]),
                Paragraph(r["currency"], styles["TdCenter"]),
            ])
        contrib_data.append([
            Paragraph("TOTAL DES APPORTS REÇUS", styles["TotalLabel"]),
            Paragraph("", styles["TotalLabel"]),
            Paragraph(f"{len(contributions)} apports enregistrés", styles["TotalLabel"]),
            Paragraph("", styles["TotalLabel"]),
            Paragraph("", styles["TotalLabel"]),
            Paragraph(fmt_money(tot_contrib_sec), styles["TotalValue"]),
            Paragraph(contrib_curr, styles["TotalCenter"]),
        ])
        c_widths = [65, 120, 130, 120, 110, 85, 45]
        scale = usable_width / sum(c_widths)
        c_widths = [w * scale for w in c_widths]
        contrib_table = Table(contrib_data, colWidths=c_widths, repeatRows=1, hAlign="LEFT")
        contrib_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PRIMARY_GREEN),
            ("GRID", (0, 0), (-1, -2), 0.5, BORDER_COLOR),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [WHITE, ROW_ALT]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("BACKGROUND", (0, -1), (-1, -1), DARK_GREEN),
            ("LINEABOVE", (0, -1), (-1, -1), 1, GOLD),
            ("SPAN", (0, -1), (1, -1)),
        ]))
        story.append(contrib_table)

    story.append(Spacer(1, 3 * mm))

    # =========================================================================
    # 5. COMMISSIONS PARTENAIRES & 6. DIVIDENDES ET PAIEMENTS
    # =========================================================================
    story.append(Paragraph("5. Commissions partenaires", styles["SectionHeading"]))
    partner_comms = snapshot.get("partner_commissions", [])

    if partner_comms:
        pc_headers = [
            Paragraph("Partenaire", styles["Th"]),
            Paragraph("Opération réf.", styles["ThCenter"]),
            Paragraph("Part contractuelle", styles["ThRight"]),
            Paragraph("Commission partenaire", styles["ThRight"]),
            Paragraph("Devise", styles["ThCenter"]),
        ]
        pc_data = [pc_headers]
        tot_pc = Decimal("0")
        pc_curr = "USD"
        for r in partner_comms:
            amt = Decimal(r["share"])
            tot_pc += amt
            pc_curr = r["currency"]
            op_ref_raw = r["operation"]
            op_short = f"[{op_ref_raw[:8]}]" if len(op_ref_raw) > 12 else op_ref_raw
            pc_data.append([
                Paragraph(escape_xml(r["partner"]), styles["TdBold"]),
                Paragraph(op_short, styles["TdCenter"]),
                Paragraph(fmt_percent(r["share_percent"]), styles["TdRight"]),
                Paragraph(fmt_money(amt), styles["TdRight"]),
                Paragraph(r["currency"], styles["TdCenter"]),
            ])
        pc_data.append([
            Paragraph("TOTAL COMMISSIONS PARTENAIRES", styles["TotalLabel"]),
            Paragraph(f"{len(partner_comms)} opérations", styles["TotalCenter"]),
            Paragraph("", styles["TotalLabel"]),
            Paragraph(fmt_money(tot_pc), styles["TotalValue"]),
            Paragraph(pc_curr, styles["TotalCenter"]),
        ])
        pc_widths = [200, 140, 100, 100, usable_width - 200 - 140 - 100 - 100]
        pc_table = Table(pc_data, colWidths=pc_widths, repeatRows=1, hAlign="LEFT")
        pc_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PRIMARY_GREEN),
            ("GRID", (0, 0), (-1, -2), 0.5, BORDER_COLOR),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [WHITE, ROW_ALT]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("BACKGROUND", (0, -1), (-1, -1), DARK_GREEN),
            ("LINEABOVE", (0, -1), (-1, -1), 1, GOLD),
        ]))
        story.append(pc_table)
    else:
        empty_box = Table(
            [[Paragraph("<i>Aucune commission partenaire enregistrée sur cette période.</i>", styles["NoticeEmpty"])]],
            colWidths=[usable_width], hAlign="LEFT"
        )
        empty_box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
            ("BOX", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("PADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(empty_box)

    story.append(Spacer(1, 3 * mm))

    # =========================================================================
    # 6. DIVIDENDES ET PAIEMENTS
    # =========================================================================
    story.append(Paragraph("6. Dividendes et paiements", styles["SectionHeading"]))
    distributions = snapshot.get("distributions", [])

    if distributions:
        dist_headers = [
            Paragraph("Bénéficiaire", styles["Th"]),
            Paragraph("Type", styles["ThCenter"]),
            Paragraph("Montant alloué", styles["ThRight"]),
            Paragraph("Devise", styles["ThCenter"]),
            Paragraph("Statut", styles["ThCenter"]),
        ]
        dist_data = [dist_headers]
        tot_dist_sec = Decimal("0")
        dist_curr = "USD"
        for r in distributions:
            amt = Decimal(r["amount"])
            tot_dist_sec += amt
            dist_curr = r["currency"]
            dist_data.append([
                Paragraph(escape_xml(r["party"]), styles["TdBold"]),
                Paragraph(escape_xml(r["type"]), styles["TdCenter"]),
                Paragraph(fmt_money(amt), styles["TdRight"]),
                Paragraph(r["currency"], styles["TdCenter"]),
                Paragraph(escape_xml(r["status"]), styles["TdCenter"]),
            ])
        dist_data.append([
            Paragraph("TOTAL DISTRIBUTIONS EFFECTUÉES", styles["TotalLabel"]),
            Paragraph(f"{len(distributions)} distributions", styles["TotalCenter"]),
            Paragraph(fmt_money(tot_dist_sec), styles["TotalValue"]),
            Paragraph(dist_curr, styles["TotalCenter"]),
            Paragraph("", styles["TotalCenter"]),
        ])
        dist_widths = [240, 140, 120, 50, usable_width - 240 - 140 - 120 - 50]
        dist_table = Table(dist_data, colWidths=dist_widths, repeatRows=1, hAlign="LEFT")
        dist_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PRIMARY_GREEN),
            ("GRID", (0, 0), (-1, -2), 0.5, BORDER_COLOR),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [WHITE, ROW_ALT]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("BACKGROUND", (0, -1), (-1, -1), DARK_GREEN),
            ("LINEABOVE", (0, -1), (-1, -1), 1, GOLD),
        ]))
        story.append(dist_table)
    else:
        empty_box = Table(
            [[Paragraph("<i>Aucune distribution de dividende enregistrée sur cette période.</i>", styles["NoticeEmpty"])]],
            colWidths=[usable_width], hAlign="LEFT"
        )
        empty_box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
            ("BOX", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("PADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(empty_box)

    story.append(Spacer(1, 3 * mm))

    # =========================================================================
    # 7. FICHES DES PARTIES PRENANTES
    # =========================================================================
    story.append(PageBreak())  # Clean break for stakeholder cards
    story.append(Paragraph("7. Fiches des parties prenantes", styles["SectionHeading"]))
    stakeholders = snapshot.get("stakeholders", [])

    half_card_w = (usable_width - 4 * mm) / 2.0

    cards_flowables = []
    for party in stakeholders:
        uid_short = f"[{party['identifier'][:8]}]"
        ptype = party.get("type", "Partie prenante")
        pname = party.get("name", "")
        pemail = party.get("email") or "Non renseigné"
        pphone = party.get("phone") or "Non renseigné"
        pcity = party.get("city") or "Non renseignée"
        pcontract = party.get("contract") or "Sans contrat"
        pstarts = fmt_date(party.get("starts_on"))
        pends = fmt_date(party.get("ends_on"))
        pclauses = party.get("clauses") or "Aucune clause enregistrée"
        pstatus = party.get("contract_status") or "Actif"

        # Explicit distinction of values: 0, non renseigné, N/A
        if ptype == "Investisseur":
            fin_info = (
                f"<b>Rendement contractuel :</b> {fmt_percent(party.get('return_percent'))}  &nbsp;|&nbsp;  "
                f"<b>Fréquence de paiement :</b> {party.get('payment_frequency') or 'À l\'échéance'}<br/>"
                f"<b>Parts sociales :</b> <font color='#596763'>N/A</font>  &nbsp;|&nbsp;  "
                f"<b>Dividende :</b> <font color='#596763'>N/A</font>  &nbsp;|&nbsp;  "
                f"<b>Part partenaire :</b> <font color='#596763'>N/A</font>"
            )
        elif ptype == "Actionnaire":
            shares_str = party.get("shares")
            unit_val_str = party.get("share_unit_value")
            tot_shares_val = "-"
            try:
                tot_shares_val = fmt_money(Decimal(shares_str) * Decimal(unit_val_str), "USD")
            except Exception:
                tot_shares_val = "-"
            fin_info = (
                f"<b>Parts détenues :</b> {shares_str}  &nbsp;|&nbsp;  "
                f"<b>Valeur unitaire :</b> {fmt_money(unit_val_str, 'USD')}  &nbsp;|&nbsp;  "
                f"<b>Capital :</b> {tot_shares_val}<br/>"
                f"<b>Dividende contractuel :</b> {fmt_percent(party.get('dividend_percent'))}  &nbsp;|&nbsp;  "
                f"<b>Dividende estimé :</b> {fmt_money(party.get('estimated_dividend'), 'USD')}<br/>"
                f"<b>Rendement investisseur :</b> <font color='#596763'>N/A</font>  &nbsp;|&nbsp;  "
                f"<b>Part partenaire :</b> <font color='#596763'>N/A</font>"
            )
        elif ptype == "Partenaire":
            fin_info = (
                f"<b>Part commissions :</b> {fmt_percent(party.get('partner_share_percent'))}<br/>"
                f"<b>Rendement investisseur :</b> <font color='#596763'>N/A</font>  &nbsp;|&nbsp;  "
                f"<b>Parts sociales :</b> <font color='#596763'>N/A</font>  &nbsp;|&nbsp;  "
                f"<b>Dividende :</b> <font color='#596763'>N/A</font>"
            )
        else:
            fin_info = "<font color='#596763'>Aucune condition financière enregistrée</font>"

        card_rows = [
            [
                Paragraph(f"<b>{escape_xml(pname)}</b> &nbsp;·&nbsp; <font size=7 color='#DCE3DF'>{escape_xml(ptype)}</font>", styles["CardHead"]),
                Paragraph(f"Réf. {uid_short}", styles["CardHeadRight"]),
            ],
            [
                Paragraph(
                    f"<b>Identité & Coordonnées :</b> {escape_xml(pemail)} &nbsp;·&nbsp; Tél : {escape_xml(pphone)} &nbsp;·&nbsp; Ville : {escape_xml(pcity)}",
                    styles["CardFieldValue"]
                ),
                "",
            ],
            [
                Paragraph(
                    f"<b>Contrat :</b> {escape_xml(pcontract)} ({escape_xml(pstatus)}) &nbsp;·&nbsp; "
                    f"Période : {pstarts} au {pends}<br/>"
                    f"<b>Clauses contractuelles :</b> {escape_xml(pclauses)}",
                    styles["CardFieldValue"]
                ),
                "",
            ],
            [
                Paragraph(f"<b>Conditions financières :</b><br/>{fin_info}", styles["CardFieldValue"]),
                "",
            ],
        ]

        card_table = Table(card_rows, colWidths=[half_card_w - 60, 60], hAlign="LEFT")
        card_table.setStyle(TableStyle([
            ("SPAN", (0, 1), (1, 1)),
            ("SPAN", (0, 2), (1, 2)),
            ("SPAN", (0, 3), (1, 3)),
            ("BACKGROUND", (0, 0), (-1, 0), DARK_GREEN),
            ("BACKGROUND", (0, 1), (-1, -1), LIGHT_BG),
            ("BOX", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("LINEBELOW", (0, 0), (-1, 0), 1, GOLD),
            ("LINEBELOW", (0, 1), (-1, -2), 0.35, BORDER_COLOR),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        cards_flowables.append(card_table)

    paired_cards = []
    for i in range(0, len(cards_flowables), 2):
        left_c = cards_flowables[i]
        right_c = cards_flowables[i + 1] if i + 1 < len(cards_flowables) else ""
        pair_row = Table([[left_c, right_c]], colWidths=[half_card_w, half_card_w], hAlign="LEFT")
        pair_row.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2 * mm),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2 * mm),
        ]))
        paired_cards.append(pair_row)

    for p in paired_cards:
        story.append(p)

    story.append(Spacer(1, 2 * mm))

    # Annexe de correspondance des identifiants (UUID)
    story.append(Paragraph("Annexe A : Table de correspondance des identifiants uniques des parties prenantes", styles["SubSectionHeading"]))
    annex_headers = [
        Paragraph("Repère", styles["ThCenter"]),
        Paragraph("Nom de la partie prenante", styles["Th"]),
        Paragraph("Rôle / Type", styles["Th"]),
        Paragraph("Identifiant unique complet (UUID)", styles["Th"]),
    ]
    annex_data = [annex_headers]
    for party in stakeholders:
        annex_data.append([
            Paragraph(f"[{party['identifier'][:8]}]", styles["TdCenter"]),
            Paragraph(escape_xml(party.get("name")), styles["TdBold"]),
            Paragraph(escape_xml(party.get("type")), styles["Td"]),
            Paragraph(f"<font name='Courier' size=6.5>{party['identifier']}</font>", styles["Td"]),
        ])
    annex_widths = [55, 160, 110, usable_width - 55 - 160 - 110]
    annex_table = Table(annex_data, colWidths=annex_widths, repeatRows=1, hAlign="LEFT")
    annex_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY_GREEN),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, ROW_ALT]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(annex_table)
    story.append(Spacer(1, 4 * mm))

    # =========================================================================
    # 8. JOURNAL DÉTAILLÉ DES OPÉRATIONS
    # =========================================================================
    story.append(PageBreak())  # Fresh page for the transaction ledger
    story.append(Paragraph("8. Journal détaillé des opérations", styles["SectionHeading"]))
    operations = snapshot.get("operations", [])

    if operations:
        op_headers = [
            Paragraph("Date / Heure", styles["ThCenter"]),
            Paragraph("Réf.", styles["ThCenter"]),
            Paragraph("Type", styles["ThCenter"]),
            Paragraph("Service", styles["Th"]),
            Paragraph("Client", styles["Th"]),
            Paragraph("Identifiant client", styles["Th"]),
            Paragraph("Montant", styles["ThRight"]),
            Paragraph("Dev.", styles["ThCenter"]),
            Paragraph("Commission", styles["ThRight"]),
            Paragraph("Agent traitant", styles["Th"]),
        ]
        op_data = [op_headers]
        tot_op_amt = Decimal("0")
        tot_op_fees = Decimal("0")
        op_curr = "USD"

        for op in operations:
            amt = Decimal(op["amount"])
            fee = Decimal(op["commission"])
            op_curr = op["currency"]
            if op.get("status") in {"Terminée", "Complétée", "COMPLETED"}:
                tot_op_amt += amt
                tot_op_fees += fee

            ref_raw = op["reference"]
            ref_display = f"[{ref_raw[:8]}]" if len(ref_raw) > 12 else ref_raw

            op_data.append([
                Paragraph(op["date"], styles["TdCenter"]),
                Paragraph(ref_display, styles["TdCenter"]),
                Paragraph(escape_xml(op["type"]), styles["TdCenter"]),
                Paragraph(escape_xml(op["service"]), styles["Td"]),
                Paragraph(escape_xml(op["client"] or "-"), styles["Td"]),
                Paragraph(escape_xml(op["identifier"] or "-"), styles["Td"]),
                Paragraph(fmt_money(amt), styles["TdRight"]),
                Paragraph(op["currency"], styles["TdCenter"]),
                Paragraph(fmt_money(fee), styles["TdRight"]),
                Paragraph(escape_xml(op["agent"]), styles["Td"]),
            ])

        # Summary total row
        op_data.append([
            Paragraph("TOTAL CONSOLIDÉ DES OPÉRATIONS", styles["TotalLabel"]),
            Paragraph(f"{len(operations)} ops", styles["TotalCenter"]),
            Paragraph("", styles["TotalCenter"]),
            Paragraph("", styles["TotalLabel"]),
            Paragraph("", styles["TotalLabel"]),
            Paragraph("", styles["TotalLabel"]),
            Paragraph(fmt_money(tot_op_amt), styles["TotalValue"]),
            Paragraph(op_curr, styles["TotalCenter"]),
            Paragraph(fmt_money(tot_op_fees), styles["TotalValue"]),
            Paragraph("Commissions validées", styles["TotalLabel"]),
        ])

        op_widths = [66, 56, 50, 62, 115, 85, 68, 32, 58, usable_width - (66 + 56 + 50 + 62 + 115 + 85 + 68 + 32 + 58)]
        op_table = Table(op_data, colWidths=op_widths, repeatRows=1, hAlign="LEFT")
        op_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PRIMARY_GREEN),
            ("GRID", (0, 0), (-1, -2), 0.5, BORDER_COLOR),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [WHITE, ROW_ALT]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("BACKGROUND", (0, -1), (-1, -1), DARK_GREEN),
            ("LINEABOVE", (0, -1), (-1, -1), 1, GOLD),
            ("SPAN", (0, -1), (0, -1)),
        ]))
        story.append(op_table)

        # Annexe B: Table de correspondance des références complètes d'opérations (UUID)
        story.append(PageBreak())
        story.append(Paragraph("Annexe B : Table de correspondance des références uniques d'opérations (UUID)", styles["SectionHeading"]))
        annex_b_headers = [
            Paragraph("Repère", styles["ThCenter"]),
            Paragraph("Date / Heure", styles["ThCenter"]),
            Paragraph("Client / Bénéficiaire", styles["Th"]),
            Paragraph("Montant", styles["ThRight"]),
            Paragraph("Dev.", styles["ThCenter"]),
            Paragraph("Référence unique complète (UUID système)", styles["Th"]),
        ]
        annex_b_data = [annex_b_headers]
        for op in operations:
            ref_raw = op["reference"]
            ref_display = f"[{ref_raw[:8]}]" if len(ref_raw) > 12 else ref_raw
            annex_b_data.append([
                Paragraph(ref_display, styles["TdCenter"]),
                Paragraph(op["date"], styles["TdCenter"]),
                Paragraph(escape_xml(op["client"] or "-"), styles["Td"]),
                Paragraph(fmt_money(op["amount"]), styles["TdRight"]),
                Paragraph(op["currency"], styles["TdCenter"]),
                Paragraph(f"<font name='Courier' size=6.5>{ref_raw}</font>", styles["Td"]),
            ])
        b_widths = [55, 75, 140, 65, 32, usable_width - (55 + 75 + 140 + 65 + 32)]
        annex_b_table = Table(annex_b_data, colWidths=b_widths, repeatRows=1, hAlign="LEFT")
        annex_b_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PRIMARY_GREEN),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, ROW_ALT]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(annex_b_table)
    else:
        empty_box = Table(
            [[Paragraph("<i>Aucune opération enregistrée sur cette période.</i>", styles["NoticeEmpty"])]],
            colWidths=[usable_width], hAlign="LEFT"
        )
        empty_box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
            ("BOX", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("PADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(empty_box)

    # Build document using ProfessionalNumberedCanvas
    canvas_factory = lambda *args, **kwargs: ProfessionalNumberedCanvas(
        *args, downloader=downloader_name, logo_path=logo_path, **kwargs
    )
    doc.build(story, canvasmaker=canvas_factory)
    return stream.getvalue()


admin = User.objects.filter(is_superuser=True).first() or User.objects.filter(role="ADMIN").first()
print("Generating revised test report for admin:", admin)
snapshot = monthly_financial_snapshot(user=admin, year=2026, month=9)
pdf_bytes = build_monthly_financial_pdf(snapshot, admin)
out_path = Path("scratch/test_report_sept_2026.pdf")
out_path.write_bytes(pdf_bytes)
print(f"Revised PDF saved to {out_path} ({len(pdf_bytes)} bytes)")
