from django import forms

from .models import FinancialAccount, FinancialPeriod, EconomicRule, EntrySide, ServiceCatalog
from apps.stakeholders.models import Stakeholder, StakeholderRole


class FinancialAccountForm(forms.ModelForm):
    class Meta:
        model = FinancialAccount
        fields = ["code", "name", "service", "country_code", "account_type", "nature", "currency", "responsible_user", "economic_owner", "ceiling", "notes", "is_active"]

    def clean_country_code(self):
        return (self.cleaned_data.get("country_code") or "").strip().upper()


class FinancialPeriodForm(forms.ModelForm):
    class Meta:
        model = FinancialPeriod
        fields = ["period_type", "start_date", "end_date"]
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"}), "end_date": forms.DateInput(attrs={"type": "date"})}


class PartyForm(forms.ModelForm):
    class Meta:
        model = Stakeholder
        fields = ["name", "type", "owner", "email", "phone", "address", "started_on", "notes"]
        labels = {"type": "Type de partie prenante", "owner": "Compte utilisateur associé (facultatif)", "name": "Nom ou raison sociale"}
        help_texts = {"owner": "Sélectionnez un compte déjà créé par l’administrateur. Ce dossier ne crée pas d’identifiants de connexion."}

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if name.casefold() in {"mpoto jenovic", "jenovic mpoto"}:
            name = "Mpoto Jenovic"
        if Stakeholder.objects.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Cette identité existe déjà ; utilisez la partie existante.")
        return name


class RoleForm(forms.ModelForm):
    class Meta:
        model = StakeholderRole
        fields = ["stakeholder", "role", "effective_from", "effective_to"]
        widgets = {name: forms.DateInput(attrs={"type": "date"}) for name in ("effective_from", "effective_to")}


class RuleForm(forms.ModelForm):
    class Meta:
        model = EconomicRule
        fields = ["stakeholder", "kind", "value", "currency", "effective_from", "effective_to"]
        widgets = {name: forms.DateInput(attrs={"type": "date"}) for name in ("effective_from", "effective_to")}


class ServiceForm(forms.ModelForm):
    class Meta:
        model = ServiceCatalog
        fields = ["code", "name", "notes"]


class BatchForm(forms.Form):
    idempotency_key = forms.CharField(max_length=120, widget=forms.HiddenInput)
    description = forms.CharField(label="Justification", max_length=255)
    effective_at = forms.DateTimeField(label="Date comptable (avec fuseau, ex. +03:00)")


class EntryForm(forms.Form):
    account = forms.ModelChoiceField(label="Compte", queryset=FinancialAccount.objects.filter(is_active=True, legacy_cash_account__isnull=True, legacy_global_account__isnull=True).select_related("currency"))
    side = forms.ChoiceField(label="Sens", choices=EntrySide.choices)
    amount = forms.DecimalField(label="Montant", min_value=0.01, decimal_places=2, max_digits=18)


EntryFormSet = forms.formset_factory(EntryForm, extra=4, min_num=2, validate_min=True, max_num=20, validate_max=True)


class CountForm(forms.Form):
    account_id = forms.ModelChoiceField(label="Compte", queryset=FinancialAccount.objects.filter(account_type__in=["AGENT_CASH", "GLOBAL_CASH", "MOBILE_MONEY", "DIGITAL", "FINANCIAL_SERVICE"]))
    declared = forms.DecimalField(label="Solde réellement compté", required=False, min_value=0, decimal_places=2, max_digits=18)
    justification = forms.CharField(label="Justification de l’écart", required=False, widget=forms.Textarea)


class PaymentForm(forms.Form):
    account_id = forms.ModelChoiceField(label="Compte de paiement", queryset=CountForm.base_fields["account_id"].queryset.filter(is_active=True))
    amount = forms.DecimalField(label="Montant", min_value=0.01, decimal_places=2, max_digits=18)
    destination = forms.ChoiceField(label="Destination", required=False, choices=[
        ("PAYOUT", "Versement à l’actionnaire"),
        ("REINVEST", "Réinvestissement dans le capital"),
    ])
    idempotency_key = forms.CharField(widget=forms.HiddenInput, max_length=120)

    def clean(self):
        data = super().clean()
        if "destination" in self.fields:
            data["destination"] = data.get("destination") or "PAYOUT"
            if data["destination"] == "PAYOUT" and not data.get("account_id"):
                self.add_error("account_id", "Un compte de paiement est requis pour un versement.")
        return data


class ReasonForm(forms.Form):
    reason = forms.CharField(label="Motif", min_length=5, max_length=255)


class DistributionForm(forms.Form):
    amount = forms.DecimalField(label="Montant proposé à la distribution", min_value=0.01, decimal_places=2, max_digits=18)


class ImportReviewForm(forms.Form):
    resolution = forms.CharField(label="Décision et référence de la pièce justificative", min_length=20, widget=forms.Textarea)
    confirmed = forms.BooleanField(label="J’ai vérifié les anomalies et documenté leur résolution ou l’exclusion de cette source.")
