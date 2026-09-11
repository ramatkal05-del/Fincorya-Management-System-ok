from django.contrib import admin
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet
from config.admin import ValidatedServiceAdmin

from .models import Currency, ExchangeRate, TariffSchedule, TariffTier


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ("code", "is_active")


@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ("currency", "rate_to_usd", "effective_at", "created_by")
    list_filter = ("currency",)

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


class TariffTierFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        rows = sorted(
            (form.cleaned_data for form in self.forms
             if form.cleaned_data and not form.cleaned_data.get("DELETE")),
            key=lambda row: row["min_amount"],
        )
        for left, right in zip(rows, rows[1:]):
            if right["min_amount"] <= left["max_amount"]:
                raise ValidationError("Les tranches tarifaires ne peuvent pas se chevaucher.")


class TariffTierInline(admin.TabularInline):
    model = TariffTier
    formset = TariffTierFormSet
    extra = 1


@admin.register(TariffSchedule)
class TariffScheduleAdmin(ValidatedServiceAdmin):
    list_display = ("name", "currency", "is_published")
    inlines = (TariffTierInline,)
