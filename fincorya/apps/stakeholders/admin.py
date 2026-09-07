from django.contrib import admin

from apps.contracts.models import Contract
from .models import Investment, PartnerOperation, Stakeholder


class InvestmentInline(admin.TabularInline):
    model = Investment
    extra = 0


class ContractInline(admin.StackedInline):
    model = Contract
    extra = 0
    fields = ("title", "starts_on", "ends_on", "status", "clauses", "created_by")


@admin.register(Stakeholder)
class StakeholderAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "email", "owner", "dividend_percent", "partner_share_percent", "is_active")
    list_filter = ("type", "is_active")
    search_fields = ("name", "email", "owner__email")
    fieldsets = (
        ("Identité", {"fields": ("name", "type", "owner", "email", "is_active")}),
        ("Investisseur", {"fields": ("investor_return_percent", "payment_frequency")}),
        ("Actionnaire", {"fields": ("share_count", "share_unit_value", "dividend_percent")}),
        ("Partenaire", {"fields": ("partner_share_percent",)}),
    )
    inlines = (ContractInline, InvestmentInline)


admin.site.register(PartnerOperation)
