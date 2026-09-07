from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Role, User
from apps.reports.services import _monthly_pdf, _pdf_bytes, monthly_financial_snapshot, operation_report_snapshot


class Command(BaseCommand):
    help = "Régénère les deux aperçus PDF FINCORYA avec le moteur de rapport courant."

    def handle(self, *args, **options):
        user = User.objects.filter(role=Role.ADMIN, is_active=True).order_by("id").first()
        if user is None:
            raise CommandError("Créez un administrateur avant de générer les aperçus.")

        output_dir = Path(settings.BASE_DIR) / "output" / "pdf"
        output_dir.mkdir(parents=True, exist_ok=True)
        today = date.today()

        operations = operation_report_snapshot(user=user, start_date=today - timedelta(days=30), end_date=today)
        operations_path = output_dir / "fincorya-operations-report-preview.pdf"
        operations_path.write_bytes(_pdf_bytes(operations, user))

        monthly = monthly_financial_snapshot(user=user, year=today.year, month=today.month)
        monthly_path = output_dir / "fincorya-report-preview.pdf"
        monthly_path.write_bytes(_monthly_pdf(monthly, user))

        self.stdout.write(self.style.SUCCESS(f"Aperçus générés : {operations_path} ; {monthly_path}"))
