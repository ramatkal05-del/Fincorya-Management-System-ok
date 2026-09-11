from django.contrib import admin

from .models import Operation, OperationRevision, TransactionServiceOption


@admin.register(TransactionServiceOption)
class TransactionServiceOptionAdmin(admin.ModelAdmin):
    list_display = ("label", "code", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("code", "label")


@admin.register(Operation)
class OperationAdmin(admin.ModelAdmin):
    list_display = ("reference", "created_at", "agent", "type", "service", "customer_name", "amount", "currency", "fee", "status")
    list_filter = ("type", "service", "status", "currency", "created_at")
    search_fields = ("reference", "agent__email", "customer_name", "customer_identifier")
    readonly_fields = tuple(field.name for field in Operation._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        # Corrections must go through the audited revise_operation/cancel_operation
        # services (module Opérations), never a raw admin save.
        return False if obj else super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OperationRevision)
class OperationRevisionAdmin(admin.ModelAdmin):
    list_display = ("operation", "revised_by", "reason", "created_at")
    readonly_fields = tuple(field.name for field in OperationRevision._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        # OperationRevision.save() raises ValueError on update (immutable
        # ledger); without this, submitting the (fieldless) change form still
        # calls save() and crashes with an uncaught 500.
        return False if obj else super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False
