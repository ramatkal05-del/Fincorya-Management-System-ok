from django.contrib import admin
from django.utils import timezone
from config.admin import ValidatedServiceAdmin

from apps.audit.services import record
from .models import CashAccount, CashFunding, CashMovement, GlobalCashAccount, GlobalCashMovement
from .services import adjust_global_cash, allocate_cash


@admin.register(CashAccount)
class CashAccountAdmin(ValidatedServiceAdmin):
    list_display = ("agent", "currency", "global_account", "balance", "is_active", "allocated_by", "allocated_at")
    list_filter = ("is_active", "currency")
    search_fields = ("agent__email", "agent__first_name", "agent__last_name")
    autocomplete_fields = ("agent", "global_account")

    def get_readonly_fields(self, request, obj=None):
        return ("allocated_by", "allocated_at", "balance")

    def save_model(self, request, obj, form, change):
        activated = obj.is_active and (not change or "is_active" in form.changed_data)
        if activated:
            obj.allocated_by = request.user
            obj.allocated_at = timezone.now()
        super().save_model(request, obj, form, change)
        record(actor=request.user, action="CASH_ALLOCATE" if activated else "CASH_UPDATE", instance=obj,
               after={"agent": obj.agent_id, "currency": obj.currency.code, "balance": str(obj.balance), "active": obj.is_active})


@admin.register(GlobalCashAccount)
class GlobalCashAccountAdmin(ValidatedServiceAdmin):
    list_display = ("currency", "administrator", "balance", "capital", "is_active", "created_at")
    list_filter = ("is_active", "currency")
    search_fields = ("currency__code", "administrator__email")
    autocomplete_fields = ("administrator",)
    readonly_fields = ("balance", "capital", "created_at")

    def save_model(self, request, obj, form, change):
        if not change and not obj.administrator_id:
            obj.administrator = request.user
        super().save_model(request, obj, form, change)
        record(actor=request.user, action="GLOBAL_CASH_CONFIGURATION", instance=obj,
               after={"currency": obj.currency.code, "active": obj.is_active, "capital": str(obj.capital)})


@admin.register(GlobalCashMovement)
class GlobalCashMovementAdmin(ValidatedServiceAdmin):
    list_display = ("global_account", "direction", "movement_type", "amount", "balance_after", "created_by", "created_at")
    list_filter = ("direction", "movement_type", "global_account__currency")
    search_fields = ("global_account__currency__code", "note", "created_by__email")

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return tuple(field.name for field in GlobalCashMovement._meta.fields)
        return ("movement_type", "balance_after", "funding", "handover", "created_by", "created_at")

    def save_model(self, request, obj, form, change):
        if change:
            return
        movement = adjust_global_cash(
            global_account_id=obj.global_account_id, direction=obj.direction,
            amount=obj.amount, adjusted_by=request.user, note=obj.note,
        )
        obj.pk = movement.pk

    def has_change_permission(self, request, obj=None):
        return False if obj else super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CashMovement)
class CashMovementAdmin(admin.ModelAdmin):
    list_display = ("account", "direction", "movement_type", "amount", "balance_after", "created_at")
    readonly_fields = tuple(field.name for field in CashMovement._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        # All fields are readonly and CashMovement.save() rejects any update
        # with a ValidationError; without this, submitting the (fieldless)
        # change form still calls save() and crashes with an uncaught 500.
        return False if obj else super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CashFunding)
class CashFundingAdmin(ValidatedServiceAdmin):
    list_display = ("account", "amount", "allocated_by", "applied_at", "created_at")
    readonly_fields = ("allocated_by", "applied_at", "created_at")

    def save_model(self, request, obj, form, change):
        if change:
            return
        funding = allocate_cash(account_id=obj.account_id, amount=obj.amount, allocated_by=request.user, note=obj.note)
        obj.pk = funding.pk

    def has_change_permission(self, request, obj=None):
        return False if obj else super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False
