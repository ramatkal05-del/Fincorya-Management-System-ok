from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import router, transaction


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
