from django.contrib import admin

from apps.contracts.models import Contract
from .models import Investment, PartnerOperation, Stakeholder, StakeholderType
from apps.finance.forms import PartyForm
from django.template.response import TemplateResponse


class InvestmentInline(admin.TabularInline):
    model = Investment
    extra = 0


class ContractInline(admin.StackedInline):
    model = Contract
    extra = 0
    fields = ("title", "starts_on", "ends_on", "status", "clauses", "created_by")


@admin.register(Stakeholder)
class StakeholderAdmin(admin.ModelAdmin):
    form = PartyForm
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

    def add_view(self, request, form_url="", extra_context=None):
        if request.method == "GET" and request.GET.get("type") not in StakeholderType.values:
            if not self.has_add_permission(request):
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied
            return TemplateResponse(request, "admin/stakeholders/select_type.html", {
                **self.admin_site.each_context(request), "title": "Nouveau dossier de partie prenante",
                "opts": self.model._meta, "party_types": StakeholderType.choices})
        return super().add_view(request, form_url, extra_context)

    def get_fieldsets(self, request, obj=None):
        kind = obj.type if obj else request.POST.get("type", request.GET.get("type", "PARTNER"))
        sections = [self.fieldsets[0]]
        section = {"INVESTOR": 1, "SHAREHOLDER": 2, "PARTNER": 3}.get(kind)
        if section is not None:
            sections.append(self.fieldsets[section])
        return tuple(sections)

    def get_inlines(self, request, obj):
        kind = obj.type if obj else request.POST.get("type", request.GET.get("type"))
        return (ContractInline, InvestmentInline) if kind == "INVESTOR" else (ContractInline,)



admin.site.register(PartnerOperation)
