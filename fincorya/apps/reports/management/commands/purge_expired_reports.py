from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.audit.services import record
from apps.reports.models import ReportExport


class Command(BaseCommand):
    help = "Supprime les fichiers et instantanés de rapports au-delà de la durée de conservation."

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=settings.REPORT_RETENTION_DAYS)
        purged = 0
        for export in ReportExport.objects.filter(created_at__lt=cutoff).iterator():
            record(
                actor=None, action="REPORT_PURGE", instance=export,
                before={"kind": export.kind, "format": export.format, "created_at": export.created_at.isoformat()},
            )
            if export.file:
                export.file.delete(save=False)
            export.delete()
            purged += 1
        self.stdout.write(self.style.SUCCESS(f"{purged} rapport(s) expiré(s) supprimé(s)."))
