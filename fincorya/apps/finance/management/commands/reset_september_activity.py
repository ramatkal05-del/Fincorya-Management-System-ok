"""Erase September 2026 business activity while keeping structure and capital.

Scope (business window 2026-09-01 → 2026-10-01, Europe/Istanbul):
  DELETED — operations and everything derived from them (revisions,
    idempotency requests, partner attributions, automatic commission
    conversions), all cash movements and closures of the window, internal
    transfers, stakeholder requests, expenses and their payments, profit
    periods and distributions, and the matching ledger entries/batches.
  KEPT — users, stakeholders, accounts, rules and policies, the October
    data, the capital trail (`LEGACY_OPENING` and `CONTRIBUTION` batches),
    locked periods, ImportRow records, notifications, and the audit trail.

Guards are bypassed, not weakened: every deletion uses QuerySet._raw_delete
(straight SQL — model save()/delete() overrides do not apply), so the
application's immutability stays intact for normal operation. Everything
runs inside one `ledger_atomic` transaction.

Cash balances are intentionally left untouched: they are re-declared by the
operator after the cleanup (close_cash_day with the real counted cash).

Simulation by default. `--apply` also requires `--confirm RESET-SEPTEMBRE`.
"""
import json
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count

from apps.finance.locking import ledger_atomic
from config.business_time import business_day_bounds

START, END = date(2026, 9, 1), date(2026, 10, 1)
KEEP_EVENT_TYPES = {"LEGACY_OPENING", "CONTRIBUTION"}
RESET_KEY = "RESET-SEPTEMBRE-2026"


def _raw(queryset):
    return queryset._raw_delete(queryset.db)


class Command(BaseCommand):
    help = "Efface l'activité de septembre 2026 en conservant structure et capital ; simulation par défaut."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm", default="")
        parser.add_argument("--admin", default="fincoryagroup@gmail.com")

    @ledger_atomic
    def handle(self, *args, **options):
        from apps.accounts.models import User
        from apps.audit.services import record
        from apps.cash.models import (CashAccount, CashFunding, CashHandover, CashMovement,
                                      DailyClosure, GlobalCashAccount, GlobalCashMovement)
        from apps.expenses.models import Expense, ExpenseApproval, ExpensePayment
        from apps.finance.models import (CommissionConversion, FinancialPeriod, FundContribution,
                                         InternalTransfer, JournalBatch, LedgerEntry, ImportRow,
                                         StakeholderRequest)
        from apps.operations.models import Operation, OperationRequest, OperationRevision
        from apps.profits.models import Allocation, Distribution, ProfitPeriod
        from apps.stakeholders.models import PartnerOperation

        if options["apply"] and options["confirm"] != "RESET-SEPTEMBRE":
            raise CommandError("Exécution destructive : repasser avec --apply --confirm RESET-SEPTEMBRE.")
        actor = User.objects.filter(email__iexact=options["admin"], role="ADMIN", is_active=True).first()
        if actor is None:
            raise CommandError("Administrateur actif introuvable ; préciser --admin EMAIL.")
        if ImportRow.objects.filter(source_key=RESET_KEY).exists():
            report = {"status": "DEJA_TRAITE", "reset_row": ImportRow.objects.get(source_key=RESET_KEY).pk}
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            return

        lower, upper = business_day_bounds(START, date(2026, 9, 30))
        sept_ops = Operation.objects.filter(created_at__gte=lower, created_at__lt=upper)
        target_batches = JournalBatch.objects.filter(effective_at__gte=lower, effective_at__lt=upper)
        reversing_children = JournalBatch.objects.filter(reversal_of__in=target_batches)
        batches_to_delete = JournalBatch.objects.filter(
            pk__in=(target_batches | reversing_children).values("pk")).exclude(event_type__in=KEEP_EVENT_TYPES)

        report = {"mode": "APPLY" if options["apply"] else "SIMULATION", "window": [str(START), str(END)],
                  "kept_event_types": sorted(KEEP_EVENT_TYPES), "deleted": {}, "kept": {},
                  "batches_by_event_type": {
                      "deleted": list(batches_to_delete.values("event_type").annotate(count=Count("id"))),
                      "kept": list(JournalBatch.objects.filter(effective_at__gte=lower, effective_at__lt=upper,
                                                             event_type__in=KEEP_EVENT_TYPES)
                                   .values("event_type").annotate(count=Count("id"))),
                  }}
        steps = [
            ("commission_conversions", CommissionConversion.objects.filter(operation__in=sept_ops)),
            ("partner_operations", PartnerOperation.objects.filter(operation__in=sept_ops)),
            ("operation_revisions", OperationRevision.objects.filter(operation__in=sept_ops)),
            ("operation_requests", OperationRequest.objects.filter(operation__in=sept_ops)),
            ("cash_movements", CashMovement.objects.filter(created_at__gte=lower, created_at__lt=upper)),
            ("global_cash_movements", GlobalCashMovement.objects.filter(created_at__gte=lower, created_at__lt=upper)),
            ("daily_closures", DailyClosure.objects.filter(business_date__gte=START, business_date__lt=END)),
            ("cash_fundings", CashFunding.objects.filter(created_at__gte=lower, created_at__lt=upper)),
            ("cash_handovers", CashHandover.objects.filter(created_at__gte=lower, created_at__lt=upper)),
            ("internal_transfers", InternalTransfer.objects.filter(initiated_at__gte=lower, initiated_at__lt=upper)),
            ("stakeholder_requests", StakeholderRequest.objects.filter(submitted_at__gte=lower, submitted_at__lt=upper)),
            ("expense_payments", ExpensePayment.objects.filter(expense__incurred_on__gte=START, expense__incurred_on__lt=END)),
            ("expense_approvals", ExpenseApproval.objects.filter(expense__incurred_on__gte=START, expense__incurred_on__lt=END)),
            ("expenses", Expense.objects.filter(incurred_on__gte=START, incurred_on__lt=END)),
            ("distributions", Distribution.objects.filter(allocation__period__start_date__gte=START,
                                                          allocation__period__start_date__lt=END)),
            ("allocations", Allocation.objects.filter(period__start_date__gte=START, period__start_date__lt=END)),
            ("profit_periods", ProfitPeriod.objects.filter(start_date__gte=START, start_date__lt=END)),
            ("ledger_entries", LedgerEntry.objects.filter(batch__in=batches_to_delete)),
            ("journal_batches", batches_to_delete),
            ("operations", sept_ops),
        ]
        for label, queryset in steps:
            count = queryset.count()
            report["deleted"][label] = count
            if options["apply"] and count:
                _raw(queryset)

        report["kept"] = {
            "operations_october_and_after": Operation.objects.filter(created_at__gte=upper).count(),
            "contributions": FundContribution.objects.count(),
            "september_periods": [
                {**period, "start_date": str(period["start_date"]), "end_date": str(period["end_date"])}
                for period in FinancialPeriod.objects.filter(start_date__gte=START, end_date__lt=END)
                .values("period_type", "start_date", "end_date", "status")],
            "current_balances": {
                "global": [{"currency": a["currency__code"], "balance": str(a["balance"])}
                           for a in GlobalCashAccount.objects.values("currency__code", "balance")],
                "agents": [{"agent": a["agent__email"], "currency": a["currency__code"], "balance": str(a["balance"])}
                           for a in CashAccount.objects.values("agent__email", "currency__code", "balance")],
            },
        }
        if options["apply"]:
            row = ImportRow.objects.create(
                source_key=RESET_KEY, reviewed_by=actor, payload=report,
                resolution="Activité de septembre 2026 effacée (opérations, commissions, mouvements, "
                           "charges, bénéfices, écritures non-capital). Capital, structure et octobre conservés. "
                           "Soldes de caisse inchangés : à redéclarer par clôture comptée.")
            record(actor=actor, action="SEPTEMBER_ACTIVITY_RESET", instance=row,
                   after={"deleted": report["deleted"]})
            report["reset_row"] = row.pk
        report["status"] = "OK"
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        if not options["apply"]:
            transaction.set_rollback(True)
