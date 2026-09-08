from apps.stakeholders.models import Stakeholder
import uuid
from django import forms
from apps.accounts.models import Role
from apps.cash.models import CashAccount
from apps.pricing.models import TariffSchedule
from .models import OperationType, TransactionService


class OperationForm(forms.Form):
    field_order = ["type", "account", "service", "customer_identifier", "customer_name",
                   "amount", "stakeholder", "commission_owner_confirmed", "note",
                   "tariff_schedule", "idempotency_key"]
    stakeholder = forms.ModelChoiceField(
        label="Partenaire de commission", required=False,
        queryset=Stakeholder.objects.filter(is_active=True),
        help_text="Laissez vide si les commissions reviennent uniquement a FINCORYA.")
    commission_owner_confirmed = forms.BooleanField(
        label="Je confirme l'attribution des commissions", required=False)

    # Only the two daily business actions are exposed. The legacy received
    # transfer value remains in the model so historical data stays valid.
    type = forms.ChoiceField(
        label="Type d'opération",
        choices=[
            (OperationType.SENT_TRANSFER, "Transfert"),
            (OperationType.WITHDRAWAL, "Retrait"),
        ],
        widget=forms.RadioSelect,
    )
    account = forms.ModelChoiceField(label="Caisse", queryset=CashAccount.objects.none())
    service = forms.ChoiceField(label="Service", choices=TransactionService.choices)
    customer_identifier = forms.CharField(
        label="Numéro, e-mail ou identifiant",
        max_length=254,
        widget=forms.TextInput(attrs={"autocomplete": "off", "placeholder": "+243…, e-mail ou identifiant"}),
    )
    customer_name = forms.CharField(
        label="Nom du client", max_length=180,
        widget=forms.TextInput(attrs={"autocomplete": "name", "placeholder": "Nom complet"}),
    )
    tariff_schedule = forms.ModelChoiceField(
        queryset=TariffSchedule.objects.none(), required=False, widget=forms.HiddenInput
    )
    amount = forms.DecimalField(
        label="Montant de l'opération", min_value=0.01, max_digits=16, decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal", "placeholder": "0,00"}),
    )
    note = forms.CharField(label="Note", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    idempotency_key = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        from django.conf import settings
        self.fields['commission_owner_confirmed'].required = settings.FINANCE_LEDGER_ENABLED
        accounts = CashAccount.objects.select_related("agent", "currency").filter(is_active=True)
        if user.role == Role.AGENT:
            accounts = accounts.filter(agent=user)
        elif user.role != Role.ADMIN:
            accounts = accounts.none()
        self.fields["account"].queryset = accounts
        schedules = TariffSchedule.objects.filter(is_published=True, currency__code="USD").order_by("-id")
        self.fields["tariff_schedule"].queryset = schedules
        official_id = schedules.filter(name="FINCORYA PRD V1").values_list("pk", flat=True).first()
        self.initial.setdefault("tariff_schedule", official_id or schedules.values_list("pk", flat=True).first())
        self.initial.setdefault("idempotency_key", uuid.uuid4().hex)

    def clean_tariff_schedule(self):
        """Apply the official active schedule; tariff selection is automatic."""
        schedules = TariffSchedule.objects.filter(is_published=True, currency__code="USD").order_by("-id")
        schedule = schedules.filter(name="FINCORYA PRD V1").first() or schedules.first()
        if schedule is None:
            raise forms.ValidationError("Aucune grille tarifaire FINCORYA active n'est configurée.")
        return schedule

    def clean(self):
        cleaned = super().clean()
        identifier = (cleaned.get("customer_identifier") or "").strip()
        if cleaned.get("service") == TransactionService.PAYPAL:
            try:
                forms.EmailField().clean(identifier)
            except forms.ValidationError:
                self.add_error("customer_identifier", "Pour PayPal, saisissez une adresse e-mail valide.")
        return cleaned


class OperationFilterForm(forms.Form):
    q = forms.CharField(required=False, label="Rechercher")
    type = forms.ChoiceField(required=False, choices=[("", "Tous les types"), *OperationType.choices])
    service = forms.ChoiceField(required=False, choices=[("", "Tous les services"), *TransactionService.choices])
    status = forms.ChoiceField(required=False, choices=[("", "Tous les statuts"), ("PENDING", "En attente"), ("COMPLETED", "Terminée"), ("CANCELLED", "Annulée")])


class OperationCancellationForm(forms.Form):
    reason = forms.CharField(label="Motif d'annulation", min_length=5, widget=forms.Textarea(attrs={"rows": 3}))


class OperationRevisionForm(forms.Form):
    amount = forms.DecimalField(label="Montant corrigé", min_value=0.01, max_digits=16, decimal_places=2)
    fee = forms.DecimalField(label="Commission corrigée", min_value=0, max_digits=12, decimal_places=2)
    note = forms.CharField(label="Note", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    reason = forms.CharField(label="Motif de correction", min_length=5, widget=forms.Textarea(attrs={"rows": 3}))
