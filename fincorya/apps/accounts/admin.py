from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserCreationForm, UserChangeForm
from django.utils import timezone
from config.admin import ArchiveAdminMixin

from .models import AccountActivationToken, Role, User


class AccountCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("email", "first_name", "last_name", "role", "phone", "city")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            from django import forms
            raise forms.ValidationError("Cette adresse e-mail est deja utilisee.")
        return email


class AccountChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"


@admin.register(User)
class FincoryaUserAdmin(ArchiveAdminMixin, UserAdmin):
    add_form = AccountCreationForm
    form = AccountChangeForm
    ordering = ("email",)
    list_display = ("email", "first_name", "last_name", "role", "is_active")
    list_filter = ("role", "is_active")
    search_fields = ("email", "first_name", "last_name", "phone")
    readonly_fields = ("last_login", "date_joined", "totp_enabled", "totp_confirmed_at")
    add_fieldsets = (
        ("Compte de connexion", {"fields": ("email", "first_name", "last_name", "role", "phone", "city")}),
        ("Mot de passe", {"fields": ("password1", "password2")}),
    )
    fieldsets = (
        ("Compte de connexion", {"fields": ("email", "password", "first_name", "last_name", "role", "is_active")}),
        ("Coordonnees", {"fields": ("phone", "city", "photo", "language")}),
        ("Securite", {"fields": ("totp_enabled", "totp_confirmed_at", "last_login", "date_joined")}),
    )

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_staff and (
            request.user.is_superuser or request.user.role == Role.ADMIN
            or super().has_module_permission(request)
        )

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request) and (
            request.user.is_superuser or request.user.role == Role.ADMIN
            or super().has_view_permission(request, obj)
        )

    def has_add_permission(self, request):
        return self.has_module_permission(request) and (
            request.user.is_superuser or request.user.role == Role.ADMIN
            or super().has_add_permission(request)
        )

    def has_change_permission(self, request, obj=None):
        if obj and obj.is_superuser and not request.user.is_superuser:
            return False
        if obj and obj.role == Role.ADMIN and not (request.user.is_superuser or request.user.role == Role.ADMIN):
            return False
        return self.has_module_permission(request) and (
            request.user.is_superuser or request.user.role == Role.ADMIN
            or super().has_change_permission(request, obj)
        )

    def has_delete_permission(self, request, obj=None):
        if obj and (obj.pk == request.user.pk or (obj.is_superuser and not request.user.is_superuser)):
            return False
        if obj and obj.role == Role.ADMIN and not (request.user.is_superuser or request.user.role == Role.ADMIN):
            return False
        return self.has_module_permission(request) and (
            request.user.is_superuser or request.user.role == Role.ADMIN
            or super().has_delete_permission(request, obj)
        )

    def get_fieldsets(self, request, obj=None):
        fields = super().get_fieldsets(request, obj)
        if request.user.is_superuser:
            fields += (("Autorisations Django", {
                "fields": ("is_staff", "is_superuser", "groups", "user_permissions"),
                "description": "Les groupes et permissions contrôlent l’administration Django. "
                               "Le rôle FINCORYA définit les accès aux écrans financiers.",
            }),)
        if obj and obj.role == Role.AGENT:
            fields += (("Activite agent", {"fields": ("agent_started_on", "agent_ended_on", "monthly_salary_usd")}),)
        return fields

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        if not request.user.is_superuser and request.user.role != Role.ADMIN:
            fields += ("role",)
        if obj and obj.pk == request.user.pk:
            fields += ("is_active", "is_staff", "is_superuser", "role")
        return tuple(dict.fromkeys(fields))

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            if not change or "role" in form.changed_data:
                obj.is_staff = obj.role == Role.ADMIN
            if not change:
                obj.is_superuser = False
        super().save_model(request, obj, form, change)


@admin.register(AccountActivationToken)
class AccountActivationTokenAdmin(admin.ModelAdmin):
    """Historique des liens d'activation — jamais le jeton en clair (haché en base)."""
    list_display = ("user", "created_at", "expires_at", "status")
    readonly_fields = ("user", "created_by", "created_at", "expires_at", "used_at", "invalidated_at")
    actions = ["invalider"]

    def has_add_permission(self, request):
        return False

    def status(self, obj):
        if obj.used_at:
            return "Utilisé"
        if obj.invalidated_at:
            return "Invalidé"
        if obj.expires_at < timezone.now():
            return "Expiré"
        return "Valide"

    @admin.action(description="Invalider les liens sélectionnés")
    def invalider(self, request, queryset):
        count = queryset.filter(used_at__isnull=True, invalidated_at__isnull=True).update(invalidated_at=timezone.now())
        self.message_user(request, f"{count} lien(s) invalidé(s).")
