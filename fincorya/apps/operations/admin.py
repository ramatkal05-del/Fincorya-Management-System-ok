from django.contrib import admin

from .models import Operation, OperationRevision


@admin.register(Operation)
class OperationAdmin(admin.ModelAdmin):
    list_display = ("reference", "created_at", "agent", "type", "service", "customer_name", "amount", "currency", "fee", "status")
    list_filter = ("type", "service", "status", "currency", "created_at")
    search_fields = ("reference", "agent__email", "customer_name", "customer_identifier")
    readonly_fields = tuple(field.name for field in Operation._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OperationRevision)
class OperationRevisionAdmin(admin.ModelAdmin):
    list_display = ("operation", "revised_by", "reason", "created_at")
    readonly_fields = tuple(field.name for field in OperationRevision._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
