"""Populate a local DB with fictitious FINCORYA data and generate PDF previews.

Usage (PowerShell):
    $env:DB_PORT='5433'; $env:FINANCE_LEDGER_ENABLED='True'; .\.venv\Scripts\python.exe preview_reports.py

All names and amounts are fictitious demo data.
"""
import os
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.cash.models import CashAccount, GlobalCashAccount
from apps.cash.services import adjust_global_cash, allocate_cash
from apps.expenses.models import Expense
from apps.expenses.services import decide_expense
from apps.finance.closing import (close_period, period_report, record_count,
    propose_distribution, approve_distributions, pay_distribution)
from apps.finance.funds import (record_contribution, set_partner_guarantee,
    initiate_transfer, receive_transfer, confirm_transfer, record_conversion, onboard_party)
from apps.finance.models import (AccountType, EconomicRule, FinancialAccount, FinancialPeriod,
    RemunerationTerms, DistributionPolicy)
from apps.finance.services import migrate_legacy_opening_balances, ledger_balance
from apps.finance.reporting import build_report
from apps.reports.services import _finance_pdf, monthly_financial_snapshot, _monthly_pdf
from apps.operations.services import create_sent_transfer
from apps.pricing.models import Currency, ExchangeRate, TariffSchedule, TariffTier
from apps.stakeholders.models import Stakeholder

ZONE = ZoneInfo("Europe/Istanbul")
OUT = "output/pdf"
os.makedirs(OUT, exist_ok=True)

def a(day, hour=12):
    """Return a timestamp in August (pre-cutover / legacy)."""
    return datetime(2026, 8, day, hour, tzinfo=ZONE)

def s(day, hour=12):
    """Return a timestamp in September (operating month)."""
    return datetime(2026, 9, day, hour, tzinfo=ZONE)

def t(day, hour=12):
    """Return a timestamp in August (default for cutover)."""
    return a(day, hour)

# ---------------------------------------------------------------- identities (fictitious)
def user(email, first, last, role):
    u, _ = User.objects.update_or_create(email=email, defaults={"first_name": first, "last_name": last, "role": role, "is_active": True, "is_staff": role == Role.ADMIN, "is_superuser": role == Role.ADMIN})
    u.set_password("PreviewDemo2026!")
    u.save()
    return u

admin = user("admin.preview@fincorya.local", "Amina", "Preview", Role.ADMIN)
manager = user("finance.preview@fincorya.local", "Marc", "Finance", Role.FINANCE_MANAGER)
agent1 = user("agent1.preview@fincorya.local", "Grâce", "Agent1", Role.AGENT)
agent2 = user("agent2.preview@fincorya.local", "Léa", "Agent2", Role.AGENT)

usd = Currency.objects.get_or_create(code="USD")[0]
eur = Currency.objects.get_or_create(code="EUR")[0]

# ---------------------------------------------------------------- legacy cash before cutover
with override_settings(FINANCE_LEDGER_ENABLED=False), patch("django.utils.timezone.now", return_value=t(1)):
    gusd, _ = GlobalCashAccount.objects.get_or_create(administrator=admin, currency=usd)
    geur, _ = GlobalCashAccount.objects.get_or_create(administrator=admin, currency=eur)
    cash1, _ = CashAccount.objects.get_or_create(agent=agent1, currency=usd, defaults={"global_account": gusd})
    cash2, _ = CashAccount.objects.get_or_create(agent=agent2, currency=usd, defaults={"global_account": gusd})
    if gusd.balance == 0:
        adjust_global_cash(global_account_id=gusd.pk, direction="IN", amount=Decimal("25000"), adjusted_by=admin, note="Capital initial démo")
        allocate_cash(account_id=cash1.pk, amount=Decimal("4000"), allocated_by=admin, note="Allocation démo")
        allocate_cash(account_id=cash2.pk, amount=Decimal("2500"), allocated_by=admin, note="Allocation démo")

# ---------------------------------------------------------------- cutover + accounts
with patch("django.utils.timezone.now", return_value=t(2)):
    preview = migrate_legacy_opening_balances(actor=admin, cutover_at=t(2))
    migrate_legacy_opening_balances(actor=admin, cutover_at=t(2), apply=True, expected_hash=preview["preview_hash"])

fin_cash1 = FinancialAccount.objects.get(legacy_cash_account=cash1)
fin_cash2 = FinancialAccount.objects.get(legacy_cash_account=cash2)
service_usd = FinancialAccount.objects.create(code="MPESA-USD-DEMO", name="M-Pesa USD (démo)", account_type="MOBILE_MONEY", nature="ASSET", currency=usd)
service_eur = FinancialAccount.objects.create(code="MPESA-EUR-DEMO", name="M-Pesa EUR (démo)", account_type="MOBILE_MONEY", nature="ASSET", currency=eur)

tariff = TariffSchedule.objects.get_or_create(name="Démo", currency=usd, defaults={"is_published": True})[0]
TariffTier.objects.get_or_create(schedule=tariff, min_amount=Decimal("0.01"), max_amount=Decimal("5000"), defaults={"fixed_fee": Decimal("10")})
ExchangeRate.objects.get_or_create(currency=usd, effective_at=t(1), defaults={"rate_to_usd": Decimal("1"), "created_by": admin})
ExchangeRate.objects.get_or_create(currency=eur, effective_at=t(1), defaults={"rate_to_usd": Decimal("1.10"), "created_by": admin})

# ---------------------------------------------------------------- stakeholders (fictitious)
partner = Stakeholder.objects.get_or_create(name="Partenaire Démo", type="PARTNER")[0]
EconomicRule.objects.get_or_create(stakeholder=partner, kind="COMMISSION", effective_from=date(2026, 9, 1), defaults={"value": Decimal("60"), "created_by": admin})
set_partner_guarantee(actor=admin, stakeholder_id=partner.pk, currency=usd, per_operation_ceiling=Decimal("1500"))
with patch("django.utils.timezone.now", return_value=t(3)):
    record_contribution(actor=admin, client_key="demo-partner-guarantee", origin="PARTNER_GUARANTEE", stakeholder_id=partner.pk,
        amount=Decimal("2000"), currency=usd, received_on=date(2026, 8, 3), account_id=service_usd.pk)

investor = Stakeholder.objects.get_or_create(name="Investisseur Démo", type="INVESTOR")[0]
EconomicRule.objects.get_or_create(stakeholder=investor, kind="REMUNERATION", effective_from=date(2026, 9, 1), defaults={"value": Decimal("40"), "currency": usd, "created_by": admin})
RemunerationTerms.objects.get_or_create(stakeholder=investor, defaults={"contract_start": date(2026, 9, 1), "partial_month": "PRORATA", "loss_month": "PAY", "updated_by": admin})

shareholders = []
for i in range(1, 4):
    sh, _ = Stakeholder.objects.get_or_create(name=f"Actionnaire Démo {i}", type="SHAREHOLDER")
    shareholders.append(sh)
DistributionPolicy.objects.get_or_create(mode="EQUAL_SHARES", effective_from=date(2026, 9, 1), defaults={"created_by": admin})

# ---------------------------------------------------------------- demo operations (September)
amounts = [Decimal(x) for x in ("120", "350", "80", "900", "240", "500", "150", "75", "1100", "260")]
ops = 0
for day in range(3, 31):
    for agent, cash in ((agent1, cash1), (agent2, cash2)):
        if (day + (0 if agent is agent1 else 1)) % 2:
            continue
        amount = amounts[(day * 2 + ops) % len(amounts)]
        with patch("django.utils.timezone.now", return_value=s(day, 10 + ops % 8)):
            create_sent_transfer(agent=agent, account_id=cash.pk, amount=amount, tariff_schedule=tariff,
                idempotency_key=f"demo-op-{ops}", service="AIRTEL_MONEY",
                customer_identifier=f"OP-{ops:04d}", customer_name=f"Client Démo {ops}",
                stakeholder=partner if ops % 3 == 0 else None, commission_owner_confirmed=True)
        ops += 1

# ---------------------------------------------------------------- expense, conversion, transfer, close
with patch("django.utils.timezone.now", return_value=s(20)):
    expense = Expense.objects.create(label="Frais opérationnels démo", amount=Decimal("120"), currency=usd, incurred_on=date(2026, 9, 20), created_by=admin)
    decide_expense(expense_id=expense.pk, actor=admin, decision="APPROVED")
    record_conversion(actor=admin, client_key="demo-fx-1", source_id=service_usd.pk, destination_id=service_eur.pk,
        amount_source=Decimal("500"), amount_destination=Decimal("450"), fee_source=Decimal("2"))
    transfer = initiate_transfer(actor=admin, client_key="demo-transfer-1", source_id=fin_cash1.pk, destination_id=fin_cash2.pk, amount=Decimal("600"))
    receive_transfer(actor=manager, transfer_id=transfer.pk)
    confirm_transfer(actor=admin, transfer_id=transfer.pk)

month = FinancialPeriod.objects.get_or_create(period_type="MONTH", start_date=date(2026, 9, 1), end_date=date(2026, 9, 30))[0]
for row in period_report(month)["accounts"]:
    if row["account"].account_type in {"AGENT_CASH", "GLOBAL_CASH", "MOBILE_MONEY"}:
        record_count(period_id=month.pk, account_id=row["account"].pk, declared=row["closing"], justification="", actor=admin)
close_time = datetime(2026, 10, 2, 12, tzinfo=ZoneInfo(settings.BUSINESS_TIME_ZONE))
with patch("django.utils.timezone.now", return_value=close_time):
    close_period(period_id=month.pk, actor=admin)
    from apps.profits.models import ProfitPeriod
    profit = ProfitPeriod.objects.get(finance_period=month, currency=usd)
    if profit.distributable > 0:
        propose_distribution(profit_id=profit.pk, amount=profit.distributable, actor=admin)
        approve_distributions(profit_id=profit.pk, amount=profit.distributable, actor=admin)
        from apps.profits.models import Distribution
        first = Distribution.objects.filter(allocation__period=profit).first()
        if first:
            pay_distribution(distribution_id=first.pk, account_id=fin_cash1.pk, actor=admin)

# ---------------------------------------------------------------- PDF previews
with patch("django.utils.timezone.now", return_value=s(15, 16)):
    agent_pdf = _finance_pdf(build_report(user=agent1, kind="ACTIVITY", preset="DAY", anchor=date(2026, 9, 15)), agent1)
    open(f"{OUT}/preview-agent-daily.pdf", "wb").write(agent_pdf)
    week_pdf = _finance_pdf(build_report(user=manager, kind="TREASURY", preset="WEEK", anchor=date(2026, 9, 15)), manager)
    open(f"{OUT}/preview-finance-weekly.pdf", "wb").write(week_pdf)
    monthly = monthly_financial_snapshot(user=admin, year=2026, month=9)
    open(f"{OUT}/preview-admin-monthly.pdf", "wb").write(_monthly_pdf(monthly, admin))
    result_pdf = _finance_pdf(build_report(user=admin, kind="RESULT", preset="MONTH", anchor=date(2026, 9, 15)), admin)
    open(f"{OUT}/preview-admin-result.pdf", "wb").write(result_pdf)

print("PDFs written to", os.path.abspath(OUT))
