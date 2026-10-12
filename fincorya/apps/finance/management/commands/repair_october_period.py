"""Create the missing month without replaying financial transactions."""
import json
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Sum

from apps.finance.locking import ledger_atomic


class Command(BaseCommand):
    help = "Crée octobre 2026 sans réimporter les fonds ou opérations ; simulation par défaut."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--admin", default="fincoryagroup@gmail.com")

    @ledger_atomic
    def handle(self, *args, **options):
        from apps.accounts.models import User
        from apps.audit.services import record
        from apps.finance.models import FinancialPeriod, FundContribution, ImportRow, JournalBatch
        from apps.operations.models import Operation
        from apps.stakeholders.models import Investment
        from config.business_time import business_day_bounds

        start, end = date(2026, 10, 1), date(2026, 10, 31)
        actor = User.objects.filter(email__iexact=options["admin"], role="ADMIN", is_active=True).first()
        if actor is None:
            raise CommandError("Administrateur actif introuvable ; préciser --admin EMAIL.")
        months = FinancialPeriod.objects.filter(
            period_type="MONTH", start_date__lte=end, end_date__gte=start)
        if months.exclude(start_date=start, end_date=end).exists():
            raise CommandError("Une autre période mensuelle chevauche octobre ; aucune modification.")
        period = months.filter(start_date=start, end_date=end).first()
        if period is None and FinancialPeriod.objects.filter(
                status="LOCKED", start_date__lte=end, end_date__gte=start).exists():
            raise CommandError("Une période chevauchante est verrouillée ; aucune modification.")

        lower, upper = business_day_bounds(start, end)
        imports = list(ImportRow.objects.filter(source_key__icontains="OCTOB")
                       .values("id", "source_key", "payload", "resolution"))
        # Separate the two engines: a linked legacy investment is not extra cash.
        report = {
            "mode": "APPLY" if options["apply"] else "SIMULATION",
            "period": [str(start), str(end)],
            "period_action": "EXISTANTE" if period else "CREATION",
            "period_status": period.status if period else "OPEN",
            "operations": list(Operation.objects.filter(created_at__gte=lower, created_at__lt=upper)
                .values("currency__code", "status").annotate(count=Count("id"), amount=Sum("amount"), fees=Sum("fee"))),
            "journal_batches": JournalBatch.objects.filter(effective_at__gte=lower, effective_at__lt=upper).count(),
            "legacy_investments": list(Investment.objects.filter(invested_on__lte=end)
                .values("id", "stakeholder__name", "currency__code", "amount", "invested_on")),
            "contributions": list(FundContribution.objects.filter(received_on__lte=end)
                .values("id", "stakeholder__name", "currency__code", "amount", "origin", "legacy_investment_id", "batch_id")),
            "imports": imports,
            "declared_funds_usd": {"Meschack": "7000.00", "Glodie": "4370.00", "Ruth": "3400.00",
                                   "Jenovic": "1600.00", "Fantiny": "1500.00", "Joseph": "1090.00"},
            "notice": "Les 18 960 USD sont une situation répétée dans les deux rapports, pas deux versements. "
                      "Les apports existants ne sont ni additionnés entre moteurs ni modifiés. "
                      "Les éventuels doublons déjà importés restent à rapprocher à partir de cet état. "
                      "Les rapports sélectionnent les écritures par date : aucune opération à réimporter. "
                      "Aucune date, caisse, charge ou distribution n'est modifiée.",
        }
        if options["apply"] and period is None:
            period = FinancialPeriod.objects.create(period_type="MONTH", start_date=start, end_date=end)
            record(actor=actor, action="OCTOBER_PERIOD_REPAIR", instance=period,
                   after={"start_date": str(start), "end_date": str(end), "status": period.status,
                          "financial_entries_changed": False})
        report["period_id"] = period.pk if period else None
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        if not options["apply"]:
            transaction.set_rollback(True)
