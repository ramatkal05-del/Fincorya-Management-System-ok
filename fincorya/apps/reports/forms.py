from django import forms
from django.utils import timezone
from apps.accounts.models import Role, User
from apps.stakeholders.models import Stakeholder, StakeholderType


class FinanceReportForm(forms.Form):
    """Report centre: explicit period presets with visible dates, server-side kind restriction."""
    kind = forms.ChoiceField(label="Rapport")
    preset = forms.ChoiceField(label="Période", choices=(("DAY", "Journée"), ("WEEK", "Semaine"), ("MONTH", "Mois"), ("QUARTER", "Trimestre"), ("YEAR", "Année"), ("CUSTOM", "Personnalisée")))
    anchor = forms.DateField(label="Date de référence", help_text="Pour une période personnalisée, choisissez la date de début.", widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    custom_end = forms.DateField(label="Date de fin", help_text="À renseigner uniquement pour une période personnalisée.", required=False, widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    agent = forms.ModelChoiceField(label="Agent", required=False, queryset=User.objects.none(), empty_label="Tous les agents")
    service = forms.ChoiceField(label="Service", required=False)
    status = forms.ChoiceField(label="Statut d’opération", required=False)
    country = forms.CharField(label="Pays (code)", required=False, max_length=2)
    format = forms.ChoiceField(label="Sortie", choices=(("HTML", "Afficher"), ("PDF", "PDF"), ("XLSX", "Excel"), ("CSV", "CSV")))

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.finance.reporting import allowed_kinds, can_download
        from apps.operations.models import OperationStatus
        from apps.operations.forms import OperationFilterForm
        kinds = allowed_kinds(user)
        self.fields["kind"].choices = list(kinds.items())
        self.fields["anchor"].initial = timezone.localdate()
        self.fields["agent"].queryset = User.objects.filter(role=Role.AGENT, is_active=True).order_by("first_name", "email")
        self.fields["service"].choices = OperationFilterForm().fields["service"].choices
        self.fields["status"].choices = (("", "Tous les statuts"), *OperationStatus.choices)
        if user.role == Role.AGENT:
            self.fields["preset"].choices = (("DAY", "Journée"),)
            for name in ("agent", "service", "status", "country"):
                self.fields.pop(name)
        if not can_download(user):
            self.fields["format"].choices = (("HTML", "Afficher"),)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("preset") == "CUSTOM":
            end = cleaned.get("custom_end")
            if not end or (cleaned.get("anchor") and end < cleaned["anchor"]):
                self.add_error("custom_end", "Indiquez une date de fin postérieure au début.")
            elif cleaned.get("anchor") and (end - cleaned["anchor"]).days > 366:
                self.add_error("custom_end", "La période ne peut pas dépasser 366 jours.")
        return cleaned

    def filters(self):
        return {key: self.cleaned_data.get(key) for key in ("agent", "service", "status", "country") if self.cleaned_data.get(key)}


class MonthlyReportForm(forms.Form):
    year = forms.IntegerField(label="Année", min_value=2020, max_value=2100)
    month = forms.ChoiceField(label="Mois", choices=[(i, name) for i, name in enumerate(
        ("", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre")
    ) if i])
    agent = forms.ModelChoiceField(
        label="Agent", required=False, queryset=User.objects.none(), empty_label="Tous les agents"
    )
    stakeholder = forms.ModelChoiceField(
        label="Partie prenante", required=False, queryset=Stakeholder.objects.none(), empty_label="Toutes les parties prenantes"
    )
    stakeholder_type = forms.ChoiceField(
        label="Catégorie", required=False,
        choices=(("", "Toutes les catégories"), *StakeholderType.choices),
    )
    format = forms.ChoiceField(label="Format", choices=(("PDF", "PDF"), ("XLSX", "Excel"), ("CSV", "CSV")))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        today = timezone.localdate()
        self.fields["year"].initial = today.year
        self.fields["month"].initial = today.month
        self.fields["agent"].queryset = User.objects.filter(role=Role.AGENT, is_active=True).order_by("first_name", "email")
        stakeholders = Stakeholder.objects.filter(is_active=True)
        selected_type = self.data.get("stakeholder_type") if self.is_bound else None
        if selected_type in StakeholderType.values:
            stakeholders = stakeholders.filter(type=selected_type)
        self.fields["stakeholder"].queryset = stakeholders.order_by("type", "name")

    def clean(self):
        cleaned = super().clean()
        stakeholder, stakeholder_type = cleaned.get("stakeholder"), cleaned.get("stakeholder_type")
        if stakeholder and stakeholder_type and stakeholder.type != stakeholder_type:
            self.add_error("stakeholder", "Cette partie prenante ne correspond pas à la catégorie choisie.")
        return cleaned
