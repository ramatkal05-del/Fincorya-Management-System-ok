from django.contrib import admin
from .models import AccountReconciliation, FinancialAccount, FinancialPeriod, JournalBatch, LedgerEntry, LegacyMigrationRun


class FinanceReadOnlyAdmin(admin.ModelAdmin):
    """Use the financial workspace for validated, transactional mutations."""
    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class LedgerEntryInline(admin.TabularInline):
    model = LedgerEntry
    extra = 0
    can_delete = False
    readonly_fields = tuple(field.name for field in LedgerEntry._meta.fields)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(JournalBatch)
class JournalBatchAdmin(FinanceReadOnlyAdmin):
    list_display = ("public_id", "description", "event_type", "effective_at", "status", "created_by")
    list_filter = ("status", "event_type")
    search_fields = ("description", "public_id")
    inlines = [LedgerEntryInline]


@admin.register(FinancialAccount)
class FinancialAccountAdmin(FinanceReadOnlyAdmin):
    list_display = ("code", "name", "account_type", "currency", "responsible_user", "cached_balance", "is_active")
    list_filter = ("account_type", "currency", "is_active")
    search_fields = ("code", "name", "responsible_user__email")


@admin.register(FinancialPeriod)
class FinancialPeriodAdmin(FinanceReadOnlyAdmin):
    list_display = ("period_type", "start_date", "end_date", "status")
    list_filter = ("period_type", "status")


admin.site.register(LegacyMigrationRun, FinanceReadOnlyAdmin)
admin.site.register(AccountReconciliation, FinanceReadOnlyAdmin)
