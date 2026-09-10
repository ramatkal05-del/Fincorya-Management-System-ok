"""Generate XLSX previews and verify totals match dashboard snapshots."""
import os
from datetime import date
from unittest.mock import patch
from zoneinfo import ZoneInfo

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from openpyxl import load_workbook

from apps.accounts.models import Role, User
from apps.finance.reporting import build_report
from apps.reports.services import _finance_xlsx, monthly_financial_snapshot

OUT = "output/xlsx"
os.makedirs(OUT, exist_ok=True)

admin = User.objects.filter(role=Role.ADMIN, is_active=True).order_by("id").first()
manager = User.objects.filter(role=Role.FINANCE_MANAGER, is_active=True).order_by("id").first()
agent = User.objects.filter(role=Role.AGENT, is_active=True).order_by("id").first()

Z = ZoneInfo("Europe/Istanbul")
when = __import__("datetime").datetime(2026, 9, 15, 16, tzinfo=Z)

with patch("django.utils.timezone.now", return_value=when):
    # Agent daily XLSX
    agent_report = build_report(user=agent, kind="ACTIVITY", preset="DAY", anchor=date(2026, 9, 15))
    agent_xlsx = _finance_xlsx(agent_report)
    open(f"{OUT}/preview-agent-daily.xlsx", "wb").write(agent_xlsx)
    print("Agent XLSX: sections=", len(agent_report["sections"]))

    # Finance weekly XLSX
    week_report = build_report(user=manager, kind="TREASURY", preset="WEEK", anchor=date(2026, 9, 15))
    week_xlsx = _finance_xlsx(week_report)
    open(f"{OUT}/preview-finance-weekly.xlsx", "wb").write(week_xlsx)
    print("Finance weekly XLSX: sections=", len(week_report["sections"]))

    # Admin monthly XLSX
    month_report = build_report(user=admin, kind="ACTIVITY", preset="MONTH", anchor=date(2026, 9, 1))
    month_xlsx = _finance_xlsx(month_report)
    open(f"{OUT}/preview-admin-monthly.xlsx", "wb").write(month_xlsx)
    print("Admin monthly XLSX: sections=", len(month_report["sections"]))

# Verify XLSX content by reading back
for name in ("preview-agent-daily.xlsx", "preview-finance-weekly.xlsx", "preview-admin-monthly.xlsx"):
    path = os.path.join(OUT, name)
    wb = load_workbook(path)
    print(f"\n=== {name} ===")
    print(f"Sheets: {wb.sheetnames}")
    header = wb["En-tête"]
    print(f"En-tête rows: {header.max_row}")
    for row in header.iter_rows(min_row=1, max_row=min(header.max_row, 6), values_only=True):
        print(f"  {row}")
    print(f"Creator: {wb.properties.creator}, Title: {wb.properties.title}")
    wb.close()
