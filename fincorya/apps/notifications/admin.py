from django.contrib import admin
from django.utils import timezone

from .models import Notification, NotificationDelivery, NotificationSettings
from .services import resend_failed


class NotificationDeliveryInline(admin.StackedInline):
    model = NotificationDelivery
    extra = 0
    readonly_fields = ("channel", "status", "attempts", "next_attempt_at", "last_error", "sent_at", "updated_at")
    can_delete = False


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    """Historique des envois — visibilité administrateur uniquement."""
    list_display = ("created_at", "category", "recipient", "subject", "delivery_status", "level")
    list_filter = ("category", "level")
    search_fields = ("subject", "recipient__email", "event_key")
    readonly_fields = ("recipient", "category", "subject", "body", "html_body", "cta_label", "cta_url",
                       "level", "event_key", "read_at", "created_at")
    inlines = [NotificationDeliveryInline]
    actions = ["relancer_envois_echoues"]

    def has_add_permission(self, request):
        return False

    def delivery_status(self, obj):
        delivery = getattr(obj, "delivery", None)
        return delivery.status if delivery else "—"
    delivery_status.short_description = "Statut d'envoi"

    @admin.action(description="Relancer les envois échoués (sans doublon)")
    def relancer_envois_echoues(self, request, queryset):
        delivery_ids = list(NotificationDelivery.objects.filter(notification__in=queryset, status="FAILED").values_list("pk", flat=True))
        count = resend_failed(delivery_ids)
        self.message_user(request, f"{count} envoi(s) repassé(s) en file pour une nouvelle tentative.")


@admin.register(NotificationDelivery)
class NotificationDeliveryAdmin(admin.ModelAdmin):
    list_display = ("notification", "status", "attempts", "next_attempt_at", "sent_at")
    list_filter = ("status",)
    readonly_fields = ("notification", "channel", "attempts", "sent_at", "updated_at")
    actions = ["relancer"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Relancer l'envoi (réutilise la même ligne, aucun doublon)")
    def relancer(self, request, queryset):
        count = resend_failed(list(queryset.filter(status="FAILED").values_list("pk", flat=True)))
        self.message_user(request, f"{count} envoi(s) repassé(s) en file.")


@admin.register(NotificationSettings)
class NotificationSettingsAdmin(admin.ModelAdmin):
    """Singleton : clôture hebdomadaire, fuseau horaire et activation par catégorie."""
    fieldsets = (
        ("Clôture hebdomadaire", {"fields": ("weekly_closure_weekday", "weekly_closure_time", "reminder_hours_before")}),
        ("Catégories de notification", {"fields": (
            "enable_weekly_agent_reminder", "enable_weekly_stakeholder_invite",
            "enable_salary_notifications", "enable_dividend_notifications",
            "enable_investor_notifications", "enable_partner_notifications",
            "enable_welcome_email",
        )}),
    )
    readonly_fields = ("updated_by", "updated_at")

    def has_add_permission(self, request):
        return not NotificationSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.pk = 1
        obj.updated_by = request.user
        obj.updated_at = timezone.now()
        super().save_model(request, obj, form, change)

    def changelist_view(self, request, extra_context=None):
        NotificationSettings.load()
        return super().changelist_view(request, extra_context)
