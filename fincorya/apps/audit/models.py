"""
FINCORYA append-only audit stream with actor, IP and before/after state.
"""
from django.db import models
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


class ImmutableAuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError(_("Le journal d'audit est immuable."))

    def delete(self):
        raise ValidationError(_("Le journal d'audit ne peut pas être supprimé."))


class AuditEvent(models.Model):
    actor = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, related_name="audit_events")
    action = models.CharField(max_length=50, verbose_name=_("Action"))
    model_name = models.CharField(max_length=100)
    object_id = models.CharField(max_length=64)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = ImmutableAuditQuerySet.as_manager()

    class Meta:
        verbose_name = _("Événement d'audit")
        verbose_name_plural = _("Événements d'audit")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"], name="audit_created_idx"),
            models.Index(fields=["model_name", "object_id"], name="audit_object_idx"),
        ]

    def __str__(self):
        return f"{self.action} {self.model_name}#{self.object_id} par {self.actor}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(_("Un événement d'audit est immuable."))
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(_("Un événement d'audit ne peut pas être supprimé."))
