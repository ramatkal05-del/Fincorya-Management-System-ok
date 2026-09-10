from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Role, User
from apps.finance.reporting import build_report
from apps.reports.services import _finance_pdf, _monthly_pdf, monthly_financial_snapshot


class Command(BaseCommand):
    help = "Régénère les deux aperçus PDF FINCORYA avec le moteur de rapport courant."

    def handle(self, *args, **options):
        user = User.objects.filter(role=Role.ADMIN, is_active=True).order_by("id").first()
        if user is None:
            raise CommandError("Créez un administrateur avant de générer les aperçus.")

        output_dir = Path(settings.BASE_DIR) / "output" / "pdf"
        output_dir.mkdir(parents=True, exist_ok=True)
        today = date.today()

        activity = build_report(user=user, kind="ACTIVITY", preset="MONTH", anchor=today)
        operations_path = output_dir / "fincorya-operations-report-preview.pdf"
        operations_path.write_bytes(_finance_pdf(activity, user))

        monthly = monthly_financial_snapshot(user=user, year=today.year, month=today.month)
        monthly_path = output_dir / "fincorya-report-preview.pdf"
        monthly_path.write_bytes(_monthly_pdf(monthly, user))

        self.stdout.write(self.style.SUCCESS(f"Aperçus générés : {operations_path} ; {monthly_path}"))
