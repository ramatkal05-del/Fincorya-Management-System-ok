from django.contrib import admin

from .models import Expense, ExpenseApproval


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("label", "category", "agent", "amount", "currency", "incurred_on", "status", "created_by")
    list_filter = ("category", "status", "currency", "incurred_on")
    search_fields = ("label", "created_by__email")


@admin.register(ExpenseApproval)
class ExpenseApprovalAdmin(admin.ModelAdmin):
    list_display = ("expense", "decision", "decided_by", "decided_at")
    readonly_fields = ("expense", "level", "decision", "decided_by", "comment", "decided_at")

    def has_add_permission(self, request):
        return False
