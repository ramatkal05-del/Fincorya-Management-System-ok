import uuid
from django.db import models
from django.utils.translation import gettext_lazy as _

class ContractStatus(models.TextChoices):
    DRAFT = "DRAFT", _("Brouillon")
    ACTIVE = "ACTIVE", _("Actif")
    EXPIRED = "EXPIRED", _("Expiré")
    TERMINATED = "TERMINATED", _("Résilié")

class Contract(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    stakeholder = models.ForeignKey("stakeholders.Stakeholder", on_delete=models.RESTRICT, related_name="contracts")
    title = models.CharField(max_length=180)
    clauses = models.TextField(blank=True, verbose_name=_("Clauses et conditions"))
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=ContractStatus.choices, default=ContractStatus.DRAFT)
    renewed_from = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="renewals")
    created_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(ends_on__isnull=True) | models.Q(ends_on__gte=models.F("starts_on")), name="contract_dates_valid")]

class ContractVersion(models.Model):
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveIntegerField()
    document = models.FileField(upload_to="private/contracts/%Y/%m/")
    checksum = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["contract", "version"], name="unique_contract_version")]
