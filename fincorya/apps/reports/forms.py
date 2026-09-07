from django import forms
from django.utils import timezone
from apps.accounts.models import Role, User
from apps.stakeholders.models import Stakeholder, StakeholderType


class OperationReportForm(forms.Form):
    start_date = forms.DateField(label="Du", widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(label="Au", widget=forms.DateInput(attrs={"type": "date"}))
    format = forms.ChoiceField(label="Format", choices=(("PDF", "PDF"), ("XLSX", "Excel"), ("CSV", "CSV")))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        today = timezone.localdate()
        self.fields["start_date"].initial = today
        self.fields["end_date"].initial = today

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and start > end:
            raise forms.ValidationError("La date de début doit précéder la date de fin.")
        if start and end and (end - start).days > 366:
            raise forms.ValidationError("La période ne peut pas dépasser 366 jours.")
        return cleaned


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
