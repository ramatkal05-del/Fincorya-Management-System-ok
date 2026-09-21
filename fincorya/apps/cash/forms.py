from django import forms
from django.utils import timezone


class HandoverForm(forms.Form):
    amount = forms.DecimalField(label="Montant remis", min_value=0.01, max_digits=16, decimal_places=2)


class ClosureForm(forms.Form):
    business_date = forms.DateField(label="Journée métier", initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    declared_cash = forms.DecimalField(label="Montant compté", min_value=0, max_digits=16, decimal_places=2)
    justification = forms.CharField(label="Justification de l'écart", required=False, widget=forms.Textarea(attrs={"rows": 3}))
