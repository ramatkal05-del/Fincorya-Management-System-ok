"""
FINCORYA operations domain with the lifecycle defined in section 6.
"""
import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _


class OperationType(models.TextChoices):
    SENT_TRANSFER = "SENT_TRANSFER", _("Transfert envoyé")
    RECEIVED_TRANSFER = "RECEIVED_TRANSFER", _("Transfert reçu")
    WITHDRAWAL = "WITHDRAWAL", _("Retrait")


class OperationStatus(models.TextChoices):
    PENDING = "PENDING", _("En attente")   # received, not yet paid out
    COMPLETED = "COMPLETED", _("Terminée")
    CANCELLED = "CANCELLED", _("Annulée")


class TransactionService(models.TextChoices):
    AIRTEL_MONEY = "AIRTEL_MONEY", _("Airtel Money")
    VODACOM_MPESA = "VODACOM_MPESA", _("Vodacom M-Pesa")
    AFRIMONEY = "AFRIMONEY", _("Afrimoney")
    TAPTAP_SEND = "TAPTAP_SEND", _("Tap Tap Send")
    PAYPAL = "PAYPAL", _("PayPal")
    ORANGE_MONEY = "ORANGE_MONEY", _("Orange Money")


class Operation(models.Model):
    commission_owner_confirmed = models.BooleanField(default=False)
    reference = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name=_("Référence"))
    type = models.CharField(max_length=20, choices=OperationType.choices)
    status = models.CharField(max_length=20, choices=OperationStatus.choices, default=OperationStatus.COMPLETED)
    service = models.CharField(max_length=24, choices=TransactionService.choices, default=TransactionService.AIRTEL_MONEY)
    customer_identifier = models.CharField(max_length=254, default="", verbose_name=_("Numéro, e-mail ou identifiant"))
    customer_name = models.CharField(max_length=180, default="", verbose_name=_("Nom du client"))

    agent = models.ForeignKey("accounts.User", on_delete=models.RESTRICT, related_name="operations")
    account = models.ForeignKey("cash.CashAccount", on_delete=models.RESTRICT, related_name="operations")
    currency = models.ForeignKey("pricing.Currency", on_delete=models.RESTRICT, related_name="operations")
    tariff_schedule = models.ForeignKey(
        "pricing.TariffSchedule", on_delete=models.RESTRICT, related_name="operations", null=True, blank=True
    )

    amount = models.DecimalField(max_digits=16, decimal_places=2)
    fee = models.DecimalField(max_digits=12, decimal_places=2)
    fee_auto = models.DecimalField(max_digits=12, decimal_places=2, verbose_name=_("Frais calculé (avant réduction)"))
    rate_to_usd = models.DecimalField(max_digits=14, decimal_places=6)
    amount_usd = models.DecimalField(max_digits=16, decimal_places=2)
    fee_usd = models.DecimalField(max_digits=12, decimal_places=2)

    note = models.TextField(blank=True)
    cancel_reason = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Opération")
        verbose_name_plural = _("Opérations")
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="operation_amount_positive"),
            models.CheckConstraint(condition=models.Q(fee__gte=0), name="operation_fee_non_negative"),
            models.CheckConstraint(condition=models.Q(rate_to_usd__gt=0), name="operation_rate_positive"),
        ]
        indexes = [
            models.Index(fields=["agent", "-created_at"], name="operation_agent_date_idx"),
            models.Index(fields=["status", "-created_at"], name="operation_status_date_idx"),
        ]

    def __str__(self):
        return f"{self.reference} - {self.type} - {self.amount} {self.currency.code}"


class ImmutableRevisionQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("La piste de révision est immuable.")

    def delete(self):
        raise ValueError("La piste de révision ne peut pas être supprimée.")


class OperationRevision(models.Model):
    """Correction trail — only permitted before the owning day is closed (services.revise_operation)."""
    operation = models.ForeignKey(Operation, on_delete=models.RESTRICT, related_name="revisions")
    before = models.JSONField()
    after = models.JSONField()
    reason = models.TextField()
    revised_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = ImmutableRevisionQuerySet.as_manager()

    class Meta:
        verbose_name = _("Révision d'opération")
        verbose_name_plural = _("Révisions d'opération")
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Une révision d'opération est immuable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Une révision d'opération ne peut pas être supprimée.")


class OperationRequest(models.Model):
    """Persistent idempotency reservation for critical operation creation."""
    actor = models.ForeignKey("accounts.User", on_delete=models.RESTRICT, related_name="operation_requests")
    key = models.CharField(max_length=64)
    action = models.CharField(max_length=24)
    operation = models.OneToOneField(Operation, on_delete=models.RESTRICT, null=True, blank=True, related_name="request")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["actor", "key"], name="unique_operation_idempotency_key")]
