import uuid
from django.db import models

class ReportExport(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    requested_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT)
    kind = models.CharField(max_length=40)
    format = models.CharField(max_length=4, choices=[("PDF", "PDF"), ("XLSX", "XLSX"), ("CSV", "CSV")])
    filters = models.JSONField(default=dict)
    snapshot = models.JSONField(default=dict)
    file = models.FileField(upload_to="private/reports/%Y/%m/", blank=True)
    status = models.CharField(max_length=10, choices=[("PENDING", "Pending"), ("READY", "Ready"), ("FAILED", "Failed")], default="PENDING")
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["requested_by", "-created_at"], name="report_owner_date_idx"),
            models.Index(fields=["status", "created_at"], name="report_status_date_idx"),
        ]
