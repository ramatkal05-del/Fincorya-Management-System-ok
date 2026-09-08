from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserCreationForm, UserChangeForm

from .models import Role, User


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
class FincoryaUserAdmin(UserAdmin):
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
        return request.user.is_active and request.user.role == Role.ADMIN

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.has_module_permission(request) and (obj is None or obj.pk != request.user.pk)

    def get_fieldsets(self, request, obj=None):
        fields = super().get_fieldsets(request, obj)
        if obj and obj.role == Role.AGENT:
            fields += (("Activite agent", {"fields": ("agent_started_on", "agent_ended_on", "monthly_salary_usd")}),)
        return fields

    def save_model(self, request, obj, form, change):
        obj.is_staff = obj.role == Role.ADMIN
        if obj.role != Role.ADMIN:
            obj.is_superuser = False
        super().save_model(request, obj, form, change)
