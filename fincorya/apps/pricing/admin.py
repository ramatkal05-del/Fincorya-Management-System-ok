from django.contrib import admin

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


class TariffTierInline(admin.TabularInline):
    model = TariffTier
    extra = 1


@admin.register(TariffSchedule)
class TariffScheduleAdmin(admin.ModelAdmin):
    list_display = ("name", "currency", "is_published")
    inlines = (TariffTierInline,)
