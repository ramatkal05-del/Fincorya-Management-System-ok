from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserCreationForm
from django import forms
from django.utils import timezone

from apps.contracts.models import Contract, ContractStatus
from apps.pricing.models import Currency
from apps.stakeholders.models import Investment, PaymentFrequency, Stakeholder, StakeholderType
from .models import User


class StakeholderUserCreationForm(UserCreationForm):
    contract_title = forms.CharField(label="Titre du contrat", required=False)
    contract_starts_on = forms.DateField(label="Début du contrat", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    contract_ends_on = forms.DateField(label="Échéance du contrat", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    contract_clauses = forms.CharField(label="Clauses du contrat", required=False, widget=forms.Textarea(attrs={"rows": 4}))
    investment_amount = forms.DecimalField(label="Montant investi", required=False, min_value=0.01, decimal_places=2)
    investment_currency = forms.ModelChoiceField(label="Devise", required=False, queryset=Currency.objects.all())
    invested_on = forms.DateField(label="Date d'investissement", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    investor_return_percent = forms.DecimalField(label="Rendement convenu (%)", required=False, min_value=0, decimal_places=2)
    payment_frequency = forms.ChoiceField(label="Fréquence de paiement", required=False, choices=PaymentFrequency.choices)
    share_count = forms.DecimalField(label="Nombre d'actions", required=False, min_value=0.0001, decimal_places=4)
    share_unit_value = forms.DecimalField(label="Valeur unitaire de l'action", required=False, min_value=0, decimal_places=2)
    dividend_percent = forms.DecimalField(label="Pourcentage de dividende (%)", required=False, min_value=0, max_value=100, decimal_places=2)
    partner_share_percent = forms.DecimalField(label="Part partenaire (%)", required=False, min_value=0, max_value=100, decimal_places=2, initial=40)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("email", "first_name", "last_name", "role", "phone", "city", "photo")

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        stakeholder_roles = {StakeholderType.INVESTOR, StakeholderType.SHAREHOLDER, StakeholderType.PARTNER}
        if role not in stakeholder_roles:
            return cleaned
        for field in ("contract_title", "contract_starts_on", "contract_clauses"):
            if not cleaned.get(field):
                self.add_error(field, "Ce champ est obligatoire pour cette partie prenante.")
        starts, ends = cleaned.get("contract_starts_on"), cleaned.get("contract_ends_on")
        if starts and ends and ends < starts:
            self.add_error("contract_ends_on", "L'échéance doit être postérieure au début du contrat.")
        if role == StakeholderType.INVESTOR:
            for field in ("investment_amount", "investment_currency", "invested_on", "investor_return_percent", "payment_frequency"):
                if cleaned.get(field) in (None, ""):
                    self.add_error(field, "Ce champ est obligatoire pour un investisseur.")
        elif role == StakeholderType.SHAREHOLDER:
            for field in ("share_count", "share_unit_value", "dividend_percent"):
                if cleaned.get(field) in (None, ""):
                    self.add_error(field, "Ce champ est obligatoire pour un actionnaire.")
        elif role == StakeholderType.PARTNER and cleaned.get("partner_share_percent") in (None, ""):
            self.add_error("partner_share_percent", "Ce champ est obligatoire pour un partenaire.")
        return cleaned


@admin.register(User)
class FincoryaUserAdmin(UserAdmin):
    add_form = StakeholderUserCreationForm
    ordering = ("email",)
    list_display = ("email", "first_name", "last_name", "role", "phone", "city", "is_active")
    list_filter = ("role", "city", "is_active")
    search_fields = ("email", "first_name", "last_name", "phone", "city")
    fieldsets = UserAdmin.fieldsets + (
        ("Profil FINCORYA", {"fields": ("role", "phone", "city", "photo", "language")}),
        ("Sécurité", {"fields": ("totp_enabled", "totp_confirmed_at")}),
        ("Informations agent", {"fields": ("agent_started_on", "agent_ended_on", "monthly_salary_usd")}),
    )
    add_fieldsets = (
        ("Profil FINCORYA", {"fields": ("email", "first_name", "last_name", "role", "phone", "city", "photo")}),
        ("Contrat — investisseur, actionnaire ou partenaire", {"fields": ("contract_title", "contract_starts_on", "contract_ends_on", "contract_clauses")}),
        ("Investisseur", {"fields": ("investment_amount", "investment_currency", "invested_on", "investor_return_percent", "payment_frequency")}),
        ("Actionnaire", {"fields": ("share_count", "share_unit_value", "dividend_percent")}),
        ("Partenaire", {"fields": ("partner_share_percent",)}),
    )

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if change or form.cleaned_data.get("role") not in {StakeholderType.INVESTOR, StakeholderType.SHAREHOLDER, StakeholderType.PARTNER}:
            return
        user = form.instance
        stakeholder = Stakeholder.objects.create(
            owner=user,
            name=user.get_full_name() or user.email,
            email=user.email,
            type=user.role,
            investor_return_percent=form.cleaned_data.get("investor_return_percent") or 0,
            payment_frequency=form.cleaned_data.get("payment_frequency") or PaymentFrequency.AT_MATURITY,
            share_count=form.cleaned_data.get("share_count") or 0,
            share_unit_value=form.cleaned_data.get("share_unit_value") or 0,
            dividend_percent=form.cleaned_data.get("dividend_percent") or 0,
            partner_share_percent=form.cleaned_data.get("partner_share_percent") or 0,
        )
        Contract.objects.create(
            stakeholder=stakeholder,
            title=form.cleaned_data["contract_title"],
            starts_on=form.cleaned_data["contract_starts_on"],
            ends_on=form.cleaned_data.get("contract_ends_on"),
            clauses=form.cleaned_data["contract_clauses"],
            status=ContractStatus.ACTIVE,
            created_by=request.user,
        )
        if user.role == StakeholderType.INVESTOR:
            Investment.objects.create(
                stakeholder=stakeholder,
                amount=form.cleaned_data["investment_amount"],
                currency=form.cleaned_data["investment_currency"],
                invested_on=form.cleaned_data["invested_on"],
            )
