from django import forms

from .models import (DistributionMode, DistributionPolicy, FinancialAccount, FinancialPeriod, EconomicRule, EntrySide,
                     FundOrigin, RemunerationTerms, RequestKind, ServiceCatalog)
from apps.pricing.models import Currency
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


TREASURY_QUERYSET = CountForm.base_fields["account_id"].queryset.filter(is_active=True)


class ContributionForm(forms.Form):
    client_key = forms.CharField(widget=forms.HiddenInput, max_length=120)
    origin = forms.ChoiceField(label="Origine des fonds", choices=FundOrigin.choices)
    stakeholder_id = forms.ModelChoiceField(label="Partie apporteuse", queryset=Stakeholder.objects.filter(is_active=True, type__in=["SHAREHOLDER", "INVESTOR", "PARTNER"]).order_by("type", "name"))
    amount = forms.DecimalField(label="Montant reçu", min_value=0.01, decimal_places=2, max_digits=18)
    currency = forms.ModelChoiceField(label="Devise", queryset=Currency.objects.filter(is_active=True))
    received_on = forms.DateField(label="Date de réception", widget=forms.DateInput(attrs={"type": "date"}))
    account_id = forms.ModelChoiceField(label="Compte de réception", queryset=TREASURY_QUERYSET)
    external_reference = forms.CharField(label="Référence (reçu, virement)", required=False, max_length=120)
    note = forms.CharField(label="Note", required=False, max_length=255)

    def clean(self):
        data = super().clean()
        account, currency = data.get("account_id"), data.get("currency")
        if account and currency and account.currency_id != currency.pk:
            self.add_error("account_id", "Le compte de réception doit être dans la devise de l’apport.")
        return data


class TransferForm(forms.Form):
    client_key = forms.CharField(widget=forms.HiddenInput, max_length=120)
    source_id = forms.ModelChoiceField(label="Compte source", queryset=TREASURY_QUERYSET)
    destination_id = forms.ModelChoiceField(label="Compte destination", queryset=TREASURY_QUERYSET)
    amount = forms.DecimalField(label="Montant transféré", min_value=0.01, decimal_places=2, max_digits=18)
    fee = forms.DecimalField(label="Frais de transfert (charge FINCORYA)", required=False, min_value=0, decimal_places=2, max_digits=18)
    note = forms.CharField(label="Motif", required=False, max_length=255)

    def clean(self):
        data = super().clean()
        source, destination = data.get("source_id"), data.get("destination_id")
        if source and destination and source.pk == destination.pk:
            self.add_error("destination_id", "Choisissez un compte différent de la source.")
        if source and destination and source.currency_id != destination.currency_id:
            self.add_error("destination_id", "Un transfert interne reste dans une seule devise ; utilisez une opération de change.")
        data["fee"] = data.get("fee") or 0
        return data


class GuaranteeForm(forms.Form):
    stakeholder_id = forms.ModelChoiceField(label="Partenaire", queryset=Stakeholder.objects.filter(is_active=True, type="PARTNER").order_by("name"))
    currency = forms.ModelChoiceField(label="Devise de la garantie", queryset=Currency.objects.filter(is_active=True))
    per_operation_ceiling = forms.DecimalField(label="Plafond par opération", min_value=0.01, decimal_places=2, max_digits=18)
    is_active = forms.BooleanField(label="Garantie active", required=False, initial=True)


class PolicyForm(forms.Form):
    """Admin selects existing shareholders, the mode and the effective date; percentages must total 100."""
    mode = forms.ChoiceField(label="Mode de répartition", choices=DistributionMode.choices, initial="EQUAL_SHARES")
    effective_from = forms.DateField(label="Date d’entrée en vigueur", widget=forms.DateInput(attrs={"type": "date"}))
    shareholders = forms.ModelMultipleChoiceField(label="Actionnaires concernés (déjà créés par l’admin ; vide = tous les actionnaires actifs)",
                                                  required=False, queryset=Stakeholder.objects.filter(type="SHAREHOLDER", is_active=True).order_by("name"),
                                                  widget=forms.CheckboxSelectMultiple)
    notes = forms.CharField(label="Notes", required=False, max_length=255)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for party in self.fields["shareholders"].queryset:
            self.fields[f"percent_{party.pk}"] = forms.DecimalField(label=f"Pourcentage — {party.name}", required=False, min_value=0, max_value=100, decimal_places=4, max_digits=7)

    def clean(self):
        data = super().clean()
        parties = list(data.get("shareholders") or [])
        if not parties:
            return data
        mode = data.get("mode")
        if mode == "EQUAL_SHARES":
            from decimal import Decimal, ROUND_DOWN
            equal = (Decimal("100") / len(parties)).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
            shares = {party.pk: equal for party in parties}
            shares[parties[-1].pk] = Decimal("100") - equal * (len(parties) - 1)
        elif mode == "CAPITAL_PROPORTIONAL":
            shares = {party.pk: 0 for party in parties}
        else:
            shares = {party.pk: data.get(f"percent_{party.pk}") for party in parties}
            if any(value is None for value in shares.values()):
                raise forms.ValidationError("Saisissez un pourcentage pour chaque actionnaire sélectionné.")
            if sum(shares.values()) != 100:
                raise forms.ValidationError(f"Les pourcentages totalisent {sum(shares.values())} % ; ils doivent totaliser exactement 100 %.")
        data["shares"] = shares
        return data


class PartyOnboardingForm(forms.Form):
    """Manual creation of a partner / investor / shareholder with the funds actually received."""
    party_type = forms.ChoiceField(label="Type", choices=[("PARTNER", "Partenaire"), ("INVESTOR", "Investisseur"), ("SHAREHOLDER", "Actionnaire")])
    name = forms.CharField(label="Nom ou raison sociale", max_length=180)
    email = forms.EmailField(label="E-mail", required=False)
    phone = forms.CharField(label="Téléphone", required=False, max_length=30)
    address = forms.CharField(label="Adresse", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    owner = forms.ModelChoiceField(label="Compte utilisateur associé (facultatif)", required=False, queryset=None)
    started_on = forms.DateField(label="Début du contrat", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    notes = forms.CharField(label="Informations contractuelles", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    currency = forms.ModelChoiceField(label="Devise (garantie / apport)", required=False, queryset=Currency.objects.filter(is_active=True))
    per_operation_ceiling = forms.DecimalField(label="Plafond autorisé par opération (partenaire)", required=False, min_value=0.01, decimal_places=2, max_digits=18)
    deposit_amount = forms.DecimalField(label="Dépôt / apport effectivement reçu (laisser vide si aucun)", required=False, min_value=0.01, decimal_places=2, max_digits=18)
    deposit_received_on = forms.DateField(label="Date réelle de réception", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    deposit_account = forms.ModelChoiceField(label="Compte de trésorerie qui a reçu les fonds", required=False, queryset=TREASURY_QUERYSET)
    deposit_from_opening = forms.BooleanField(label="Ce montant est déjà compris dans les soldes de reprise (aucun nouvel encaissement)", required=False)
    deposit_reference = forms.CharField(label="Référence du justificatif", required=False, max_length=120)
    deposit_evidence = forms.FileField(label="Justificatif (PDF, image)", required=False)
    client_key = forms.CharField(widget=forms.HiddenInput, max_length=120)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.accounts.models import Role, User
        self.fields["owner"].queryset = User.objects.filter(is_active=True, role__in=[Role.PARTNER, Role.INVESTOR, Role.SHAREHOLDER]).order_by("email")

    def clean(self):
        data = super().clean()
        party_type = data.get("party_type")
        if Stakeholder.objects.filter(name__iexact=(data.get("name") or "").strip()).exists():
            self.add_error("name", "Cette identité existe déjà ; utilisez le dossier existant.")
        if party_type == "PARTNER" and (not data.get("currency") or not data.get("per_operation_ceiling")):
            raise forms.ValidationError("Un partenaire exige la devise de sa garantie et un plafond par opération saisi par l’admin.")
        if data.get("deposit_amount"):
            if not data.get("currency") or not data.get("deposit_received_on"):
                raise forms.ValidationError("Un dépôt exige sa devise et sa date réelle de réception.")
            if not data.get("deposit_from_opening") and not data.get("deposit_account"):
                self.add_error("deposit_account", "Indiquez le compte qui a reçu les fonds, ou cochez « déjà compris dans la reprise ».")
            account = data.get("deposit_account")
            if account and account.currency_id != data["currency"].pk:
                self.add_error("deposit_account", "Le compte de réception doit être dans la devise du dépôt.")
        return data

    def deposit(self):
        data = self.cleaned_data
        if not data.get("deposit_amount"):
            return None
        return {"client_key": data["client_key"], "amount": data["deposit_amount"], "received_on": data["deposit_received_on"],
                "account_id": data["deposit_account"].pk if data.get("deposit_account") and not data.get("deposit_from_opening") else None,
                "from_opening_balances": bool(data.get("deposit_from_opening")), "external_reference": data.get("deposit_reference", ""),
                "evidence": data.get("deposit_evidence")}

    def identity(self):
        return {key: self.cleaned_data.get(key) or ("" if key in ("email", "phone", "address", "notes") else None)
                for key in ("name", "email", "phone", "address", "owner", "started_on", "notes")}


class RemunerationTermsForm(forms.ModelForm):
    class Meta:
        model = RemunerationTerms
        fields = ["stakeholder", "contract_start", "contract_end", "partial_month", "loss_month", "notes"]
        widgets = {name: forms.DateInput(attrs={"type": "date"}) for name in ("contract_start", "contract_end")}
        labels = {"partial_month": "Règle de mois incomplet (contrat)", "loss_month": "Règle en mois déficitaire (contrat)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stakeholder"].queryset = Stakeholder.objects.filter(is_active=True, type__in=["INVESTOR", "STAFF"]).order_by("type", "name")


class AgentCashForm(forms.Form):
    agent = forms.ModelChoiceField(label="Agent", queryset=None)
    currency = forms.ModelChoiceField(label="Devise", queryset=Currency.objects.filter(is_active=True))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.accounts.models import Role, User
        self.fields["agent"].queryset = User.objects.filter(role=Role.AGENT, is_active=True).order_by("first_name", "email")


class ConversionForm(forms.Form):
    client_key = forms.CharField(widget=forms.HiddenInput, max_length=120)
    source_id = forms.ModelChoiceField(label="Compte source", queryset=TREASURY_QUERYSET)
    destination_id = forms.ModelChoiceField(label="Compte destination (autre devise)", queryset=TREASURY_QUERYSET)
    amount_source = forms.DecimalField(label="Montant converti (devise source)", min_value=0.01, decimal_places=2, max_digits=18)
    amount_destination = forms.DecimalField(label="Montant reçu (devise destination)", min_value=0.01, decimal_places=2, max_digits=18)
    fee_source = forms.DecimalField(label="Frais (devise source)", required=False, min_value=0, decimal_places=2, max_digits=18)
    note = forms.CharField(label="Motif", required=False, max_length=255)

    def clean(self):
        data = super().clean()
        source, destination = data.get("source_id"), data.get("destination_id")
        if source and destination and source.currency_id == destination.currency_id:
            self.add_error("destination_id", "Les deux comptes doivent être dans des devises différentes.")
        data["fee_source"] = data.get("fee_source") or 0
        return data


class StakeholderRequestForm(forms.Form):
    kind = forms.ChoiceField(label="Type de demande", choices=RequestKind.choices)
    amount = forms.DecimalField(label="Montant", min_value=0.01, decimal_places=2, max_digits=18)
    currency = forms.ModelChoiceField(label="Devise", queryset=Currency.objects.filter(is_active=True))
    distribution_id = forms.ModelChoiceField(label="Distribution concernée (bénéfice)", required=False, queryset=None)
    note = forms.CharField(label="Précisions", required=False, max_length=255)

    def __init__(self, *args, party, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.profits.models import Distribution
        from .funds import REQUEST_KINDS_BY_TYPE
        allowed = REQUEST_KINDS_BY_TYPE.get(party.type, set())
        self.fields["kind"].choices = [(value, label) for value, label in RequestKind.choices if value in allowed]
        self.fields["distribution_id"].queryset = Distribution.objects.filter(stakeholder=party, approval_batch__isnull=False, status="DUE").select_related("allocation__period__currency")
        if party.type != "SHAREHOLDER":
            self.fields.pop("distribution_id")


class DecisionForm(forms.Form):
    decision = forms.ChoiceField(choices=[("APPROVED", "Valider"), ("REJECTED", "Refuser")])
    comment = forms.CharField(label="Commentaire", required=False, max_length=255)


class ExecutionForm(forms.Form):
    account_id = forms.ModelChoiceField(label="Compte de trésorerie (si mouvement de fonds)", required=False, queryset=TREASURY_QUERYSET)


class ImportReviewForm(forms.Form):
    resolution = forms.CharField(label="Décision et référence de la pièce justificative", min_length=20, widget=forms.Textarea)
    confirmed = forms.BooleanField(label="J’ai vérifié les anomalies et documenté leur résolution ou l’exclusion de cette source.")
