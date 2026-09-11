from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import router, transaction


class ArchiveAdminMixin:
    """Offer an audited alternative when financial history prevents deletion."""
    delete_confirmation_template = "admin/financial_delete_confirmation.html"
    delete_selected_confirmation_template = "admin/financial_delete_selected_confirmation.html"
    actions = ("deactivate_selected",)

    @admin.action(description="Désactiver la sélection en conservant l’historique", permissions=["change"])
    def deactivate_selected(self, request, queryset):
        from apps.audit.services import record

        changed, skipped = 0, 0
        with transaction.atomic(using=queryset.db):
            for obj in queryset.select_for_update():
                if not self.has_change_permission(request, obj) or (
                    obj._meta.label_lower == "accounts.user" and obj.pk == request.user.pk
                ):
                    skipped += 1
                    continue
                if obj.is_active:
                    obj.is_active = False
                    obj.save(update_fields=["is_active"])
                    record(actor=request.user, action="ADMIN_DEACTIVATE", instance=obj,
                           before={"is_active": True}, after={"is_active": False})
                    self.log_change(request, obj, "Désactivation avec conservation de l’historique.")
                    changed += 1
        self.message_user(request, f"{changed} élément(s) désactivé(s). L’historique a été conservé.")
        if skipped:
            self.message_user(request, f"{skipped} compte(s) protégé(s) ignoré(s).", level="warning")


class ValidatedServiceAdmin(admin.ModelAdmin):
    """Roll back a rejected service call and display its errors on the bound form."""

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        try:
            with transaction.atomic(using=router.db_for_write(self.model)):
                return super().changeform_view(request, object_id, form_url, extra_context)
        except ValidationError as exc:
            if request.method != "POST":
                raise
            request.service_validation_errors = exc.messages
            return super().changeform_view(request, object_id, form_url, extra_context)

    def get_form(self, request, obj=None, **kwargs):
        base = super().get_form(request, obj, **kwargs)
        errors = getattr(request, "service_validation_errors", None)
        if not errors:
            return base

        class RejectedServiceForm(base):
            def clean(self):
                data = super().clean()
                self.add_error(None, ValidationError(errors))
                return data

        return RejectedServiceForm
