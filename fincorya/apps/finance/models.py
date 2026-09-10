import uuid



from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from .locking import ledger_atomic, writing


class LedgerMutex(models.Model):
    pass


class ServiceOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Utilisez le service financier ; la modification en masse est interdite.")

    def delete(self):
        raise ValidationError("La suppression en masse du registre est interdite.")

    def bulk_create(self, *args, **kwargs):
        raise ValidationError("Utilisez le service financier pour créer les écritures.")

    def bulk_update(self, *args, **kwargs):
        raise ValidationError("La modification en masse du registre est interdite.")


class ServiceCatalog(models.Model):
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=100)
    notes = models.TextField(blank=True)

    def __str__(self):
        return self.name


class AccountType(models.TextChoices):
    AGENT_CASH = "AGENT_CASH", _("Caisse physique d’agent")
    MOBILE_MONEY = "MOBILE_MONEY", _("Mobile money")
    FINANCIAL_SERVICE = "FINANCIAL_SERVICE", _("Service financier")
    DIGITAL = "DIGITAL", _("Compte numérique")
    CAPITAL = "CAPITAL", _("Capital")
    COMMISSION = "COMMISSION", _("Commissions")
    EXPENSE = "EXPENSE", _("Charges")
    PARTNER_PAYABLE = "PARTNER_PAYABLE", _("Dette envers partenaire")
    TRANSIT = "TRANSIT", _("Transit")
    ADJUSTMENT = "ADJUSTMENT", _("Régularisation")
    GLOBAL_CASH = "GLOBAL_CASH", _("Caisse globale")
    EXPENSE_PAYABLE = "EXPENSE_PAYABLE", _("Charges à payer")
    DISTRIBUTION_PAYABLE = "DISTRIBUTION_PAYABLE", _("Distributions à payer")
    PARTNER_ADVANCE = "PARTNER_ADVANCE", _("Avances personnelles partenaire")
    INVESTOR_FUNDS = "INVESTOR_FUNDS", _("Fonds investisseurs")
    PARTNER_GUARANTEE = "PARTNER_GUARANTEE", _("Garanties partenaires")
    RETAINED_EARNINGS = "RETAINED_EARNINGS", _("Résultats non distribués")
    FX_DIFFERENCE = "FX_DIFFERENCE", _("Écarts de change")


class AccountNature(models.TextChoices):
    ASSET = "ASSET", _("Actif")
    LIABILITY = "LIABILITY", _("Passif")
    EQUITY = "EQUITY", _("Capitaux propres")
    INCOME = "INCOME", _("Produit")
    EXPENSE = "EXPENSE", _("Charge")


class FinancialAccount(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=180)
    service_code = models.CharField(max_length=40, blank=True)
    service = models.ForeignKey(ServiceCatalog, on_delete=models.RESTRICT, null=True, blank=True)
    country_code = models.CharField(max_length=2, blank=True)
    account_type = models.CharField(max_length=24, choices=AccountType.choices)
    nature = models.CharField(max_length=12, choices=AccountNature.choices)
    currency = models.ForeignKey("pricing.Currency", on_delete=models.RESTRICT, related_name="financial_accounts")
    responsible_user = models.ForeignKey(
        "accounts.User", on_delete=models.RESTRICT, null=True, blank=True, related_name="financial_accounts"
    )
    economic_owner = models.ForeignKey(
        "stakeholders.Stakeholder", on_delete=models.RESTRICT, null=True, blank=True, related_name="financial_accounts"
    )
    legacy_cash_account = models.OneToOneField(
        "cash.CashAccount", on_delete=models.RESTRICT, null=True, blank=True, related_name="financial_account"
    )
    legacy_global_account = models.OneToOneField("cash.GlobalCashAccount", on_delete=models.RESTRICT, null=True, blank=True)
    cached_balance = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    is_active = models.BooleanField(default=True)
    cutover_at = models.DateTimeField(null=True, blank=True)
    ceiling = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = ServiceOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(condition=models.Q(ceiling__isnull=True) | models.Q(ceiling__gt=0), name="finance_account_ceiling_positive"),
        ]

    @ledger_atomic
    def save(self, *args, **kwargs):
        if self._state.adding and self.cached_balance and not writing.get():
            raise ValidationError("Le solde initial doit provenir d’une écriture.")
        if not self._state.adding:
            previous = type(self).objects.get(pk=self.pk)
            if previous.cached_balance != self.cached_balance and not writing.get():
                raise ValidationError(_("Le solde cache ne peut être modifié que par le grand livre."))
            if self.entries.exists() and any(getattr(previous, field) != getattr(self, field) for field in ("currency_id", "nature", "legacy_cash_account_id", "legacy_global_account_id", "cutover_at")):
                raise ValidationError("La devise, nature et origine d’un compte mouvementé sont figées.")
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} — {self.name}"


class PeriodType(models.TextChoices):
    DAY = "DAY", _("Journalière")
    WEEK = "WEEK", _("Hebdomadaire")
    MONTH = "MONTH", _("Mensuelle")


class PeriodStatus(models.TextChoices):
    OPEN = "OPEN", _("Ouverte")
    LOCKED = "LOCKED", _("Verrouillée")


class FinancialPeriod(models.Model):
    period_type = models.CharField(max_length=8, choices=PeriodType.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    timezone_name = models.CharField(max_length=64, default="Europe/Istanbul")
    status = models.CharField(max_length=8, choices=PeriodStatus.choices, default=PeriodStatus.OPEN)
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT, null=True, blank=True)
    objects = ServiceOnlyQuerySet.as_manager()

    @ledger_atomic
    def save(self, *args, **kwargs):
        if self.timezone_name != "Europe/Istanbul":
            raise ValidationError("Les périodes utilisent Europe/Istanbul.")
        if self.pk:
            previous = type(self).objects.get(pk=self.pk)
            if previous.status == PeriodStatus.LOCKED:
                raise ValidationError("Une période verrouillée est immuable.")
        if self.status == PeriodStatus.LOCKED and not writing.get():
            raise ValidationError("Utilisez la clôture contrôlée pour verrouiller une période.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Les périodes du registre sont conservées.")

    class Meta:
        ordering = ["-start_date", "period_type"]
        constraints = [
            models.CheckConstraint(condition=models.Q(end_date__gte=models.F("start_date")), name="finance_period_dates_valid"),
            models.UniqueConstraint(fields=["period_type", "start_date", "end_date"], name="unique_financial_period"),
        ]


class BatchStatus(models.TextChoices):
    DRAFT = "DRAFT", _("Brouillon")
    POSTED = "POSTED", _("Publié")
    REVERSED = "REVERSED", _("Contre-passé")


class JournalBatch(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    idempotency_key = models.CharField(max_length=120, unique=True)
    event_type = models.CharField(max_length=40)
    effective_at = models.DateTimeField()
    description = models.CharField(max_length=255)
    status = models.CharField(max_length=10, choices=BatchStatus.choices, default=BatchStatus.DRAFT)
    source_model = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=64, blank=True)
    reversal_of = models.OneToOneField("self", on_delete=models.RESTRICT, null=True, blank=True, related_name="reversal")
    created_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT, related_name="journal_batches_created")
    posted_by = models.ForeignKey(
        "accounts.User", on_delete=models.RESTRICT, null=True, blank=True, related_name="journal_batches_posted"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    payload_hash = models.CharField(max_length=64, blank=True, editable=False)
    objects = ServiceOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["-effective_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_model", "source_id", "event_type"],
                condition=~models.Q(source_model="") & ~models.Q(source_id=""),
                name="unique_financial_source_event",
            )
        ]

    @ledger_atomic
    def save(self, *args, **kwargs):
        if self.status != BatchStatus.DRAFT and not writing.get():
            raise ValidationError("La publication passe par le service financier.")
        if not self._state.adding:
            previous = type(self).objects.get(pk=self.pk)
            if previous.status in {BatchStatus.POSTED, BatchStatus.REVERSED}:
                changes = {f.attname for f in self._meta.concrete_fields if getattr(previous, f.attname) != getattr(self, f.attname)}
                if not (writing.get() and previous.status == BatchStatus.POSTED and self.status == BatchStatus.REVERSED and changes == {"status"}):
                    raise ValidationError(_("Un lot publié est immuable."))
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Les lots du registre sont conservés.")


class EntrySide(models.TextChoices):
    DEBIT = "DEBIT", _("Débit")
    CREDIT = "CREDIT", _("Crédit")


class ImmutablePostedEntryQuerySet(ServiceOnlyQuerySet):
    def update(self, **kwargs):
        if self.filter(batch__status__in=[BatchStatus.POSTED, BatchStatus.REVERSED]).exists():
            raise ValidationError(_("Les écritures publiées sont immuables."))
        return super().update(**kwargs)

    def delete(self):
        if self.filter(batch__status__in=[BatchStatus.POSTED, BatchStatus.REVERSED]).exists():
            raise ValidationError(_("Les écritures publiées ne peuvent pas être supprimées."))
        return super().delete()


class LedgerEntry(models.Model):
    batch = models.ForeignKey(JournalBatch, on_delete=models.RESTRICT, related_name="entries")
    account = models.ForeignKey(FinancialAccount, on_delete=models.RESTRICT, related_name="entries")
    side = models.CharField(max_length=6, choices=EntrySide.choices)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.ForeignKey("pricing.Currency", on_delete=models.RESTRICT, related_name="ledger_entries")
    memo = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = ImmutablePostedEntryQuerySet.as_manager()

    class Meta:
        ordering = ["batch_id", "id"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="ledger_entry_amount_positive")]

    def clean(self):
        if self.account_id and self.currency_id and self.account.currency_id != self.currency_id:
            raise ValidationError({"currency": _("La devise doit correspondre à celle du compte.")})

    @ledger_atomic
    def save(self, *args, **kwargs):
        batch_ids = {self.batch_id}
        if self.pk:
            batch_ids.add(type(self).objects.filter(pk=self.pk).values_list("batch_id", flat=True).first())
        if JournalBatch.objects.filter(pk__in=batch_ids, status__in=[BatchStatus.POSTED, BatchStatus.REVERSED]).exists():
            raise ValidationError(_("Une écriture publiée est immuable."))
        self.full_clean()
        return super().save(*args, **kwargs)

    @ledger_atomic
    def delete(self, *args, **kwargs):
        batch_ids = {self.batch_id}
        if self.pk:
            batch_ids.add(type(self).objects.filter(pk=self.pk).values_list("batch_id", flat=True).first())
        if JournalBatch.objects.filter(pk__in=batch_ids, status__in=[BatchStatus.POSTED, BatchStatus.REVERSED]).exists():
            raise ValidationError(_("Une écriture publiée ne peut pas être supprimée."))
        return super().delete(*args, **kwargs)


class MigrationStatus(models.TextChoices):
    PREVIEW = "PREVIEW", _("Simulation")
    APPLIED = "APPLIED", _("Appliquée")
    FAILED = "FAILED", _("Échec")


class LegacyMigrationRun(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    cutover_at = models.DateTimeField()
    strategy = models.CharField(max_length=24, choices=[("OPENING_ONLY", _("Soldes d’ouverture uniquement")), ("HISTORY", _("Historique complet"))])
    status = models.CharField(max_length=8, choices=MigrationStatus.choices, default=MigrationStatus.PREVIEW)
    report = models.JSONField(default=dict)
    requested_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT)
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT, null=True, blank=True, related_name="approved_finance_cutovers")
    preview_hash = models.CharField(max_length=64, blank=True)


class ImportRow(models.Model):
    source_key = models.CharField(max_length=120, unique=True)
    payload = models.JSONField()
    anomalies = models.JSONField(default=list)
    resolution = models.TextField(blank=True)
    reviewed_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT, null=True, blank=True)
    imported_at = models.DateTimeField(auto_now_add=True)


class AccountCount(models.Model):
    period = models.ForeignKey(FinancialPeriod, on_delete=models.RESTRICT, related_name="counts")
    account = models.ForeignKey(FinancialAccount, on_delete=models.RESTRICT)
    theoretical = models.DecimalField(max_digits=18, decimal_places=2)
    declared = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    justification = models.TextField(blank=True)
    recorded_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["period", "account"], name="unique_finance_period_count")]


class RuleKind(models.TextChoices):
    COMMISSION = "COMMISSION", "Part partenaire (%)"
    REMUNERATION = "REMUNERATION", "Rémunération mensuelle"
    DIVIDEND = "DIVIDEND", "Part du bénéfice distribuable (%)"


class EconomicRule(models.Model):
    stakeholder = models.ForeignKey("stakeholders.Stakeholder", on_delete=models.RESTRICT, related_name="financial_rules")
    kind = models.CharField(max_length=16, choices=RuleKind.choices)
    value = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.ForeignKey("pricing.Currency", on_delete=models.RESTRICT, null=True, blank=True)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = ServiceOnlyQuerySet.as_manager()

    @ledger_atomic
    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Une règle est figée ; créez une nouvelle version datée.")
        if self.value < 0 or (self.kind != RuleKind.REMUNERATION and self.value > 100):
            raise ValidationError("Valeur de règle invalide.")
        if self.kind == RuleKind.REMUNERATION and not self.currency_id:
            raise ValidationError("La rémunération exige une devise.")
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError("Dates de règle invalides.")
        if type(self).objects.filter(stakeholder=self.stakeholder, kind=self.kind, effective_from=self.effective_from).exists():
            raise ValidationError("Une version existe déjà à cette date.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Les versions des règles sont conservées.")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["stakeholder", "kind", "effective_from"], name="unique_finance_rule_version")]


class AccountReconciliation(models.Model):
    run = models.ForeignKey(LegacyMigrationRun, on_delete=models.RESTRICT, related_name="reconciliations")
    account = models.ForeignKey(FinancialAccount, on_delete=models.RESTRICT, related_name="reconciliations")
    currency = models.ForeignKey("pricing.Currency", on_delete=models.RESTRICT)
    legacy_balance = models.DecimalField(max_digits=18, decimal_places=2)
    migrated_balance = models.DecimalField(max_digits=18, decimal_places=2)
    difference = models.DecimalField(max_digits=18, decimal_places=2)
    difference_origin = models.TextField(blank=True)
    anomalies = models.JSONField(default=list)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["run", "account"], name="unique_reconciliation_account_run")]


class FundOrigin(models.TextChoices):
    SHAREHOLDER = "SHAREHOLDER", _("Apport actionnaire")
    INVESTOR = "INVESTOR", _("Investissement")
    PARTNER_GUARANTEE = "PARTNER_GUARANTEE", _("Garantie partenaire")


class FundContribution(models.Model):
    """One received contribution, recorded once, with its economic origin and its receiving account."""
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    client_key = models.CharField(max_length=120, unique=True)
    origin = models.CharField(max_length=20, choices=FundOrigin.choices)
    stakeholder = models.ForeignKey("stakeholders.Stakeholder", on_delete=models.RESTRICT, related_name="contributions")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.ForeignKey("pricing.Currency", on_delete=models.RESTRICT)
    received_on = models.DateField()
    receiving_account = models.ForeignKey(FinancialAccount, on_delete=models.RESTRICT, related_name="received_contributions")
    # True when the funds were already part of the reconciled opening balances: the entry reclassifies
    # opening equity into the party's account and never touches treasury.
    from_opening_balances = models.BooleanField(default=False)
    external_reference = models.CharField(max_length=120, blank=True)
    evidence = models.FileField(upload_to="private/contributions/%Y/%m/", blank=True)
    note = models.CharField(max_length=255, blank=True)
    batch = models.OneToOneField(JournalBatch, on_delete=models.RESTRICT, null=True, blank=True, related_name="contribution")
    created_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT, related_name="contributions_recorded")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_on", "-id"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="contribution_amount_positive")]


class TransferStatus(models.TextChoices):
    INITIATED = "INITIATED", _("En transit")
    CONFIRMED = "CONFIRMED", _("Confirmé")
    CANCELLED = "CANCELLED", _("Annulé")


class InternalTransfer(models.Model):
    """Movement of funds between two internal accounts; the total is preserved excluding fees."""
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    client_key = models.CharField(max_length=120, unique=True)
    source = models.ForeignKey(FinancialAccount, on_delete=models.RESTRICT, related_name="transfers_out")
    destination = models.ForeignKey(FinancialAccount, on_delete=models.RESTRICT, related_name="transfers_in")
    currency = models.ForeignKey("pricing.Currency", on_delete=models.RESTRICT)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    fee = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=TransferStatus.choices, default=TransferStatus.INITIATED)
    note = models.CharField(max_length=255, blank=True)
    initiated_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT, related_name="transfers_initiated")
    initiated_at = models.DateTimeField()
    confirmed_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT, null=True, blank=True, related_name="transfers_confirmed")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    initiation_batch = models.OneToOneField(JournalBatch, on_delete=models.RESTRICT, null=True, blank=True, related_name="transfer_initiation")
    settlement_batch = models.OneToOneField(JournalBatch, on_delete=models.RESTRICT, null=True, blank=True, related_name="transfer_settlement")

    class Meta:
        ordering = ["-initiated_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="transfer_amount_positive"),
            models.CheckConstraint(condition=models.Q(fee__gte=0), name="transfer_fee_non_negative"),
            models.CheckConstraint(condition=~models.Q(source=models.F("destination")), name="transfer_distinct_accounts"),
        ]


class PartnerGuarantee(models.Model):
    """Guarantee terms of a partner; the guarantee balance itself lives in the ledger."""
    stakeholder = models.OneToOneField("stakeholders.Stakeholder", on_delete=models.RESTRICT, related_name="guarantee")
    currency = models.ForeignKey("pricing.Currency", on_delete=models.RESTRICT)
    per_operation_ceiling = models.DecimalField(max_digits=18, decimal_places=2)
    is_active = models.BooleanField(default=True)
    updated_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(per_operation_ceiling__gt=0), name="guarantee_ceiling_positive")]


class DistributionMode(models.TextChoices):
    EQUAL_SHARES = "EQUAL_SHARES", _("Parts égales entre les actionnaires sélectionnés")
    CAPITAL_PROPORTIONAL = "CAPITAL_PROPORTIONAL", _("Proportionnelle aux apports en capital")
    RULE_PERCENT = "RULE_PERCENT", _("Pourcentages des règles datées (historique)")


class DistributionPolicy(models.Model):
    """Dated, immutable distribution rule. Closed periods keep the policy that applied to them."""
    mode = models.CharField(max_length=24, choices=DistributionMode.choices)
    effective_from = models.DateField(unique=True)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = ServiceOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["-effective_from"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Une politique de distribution est figée ; créez une nouvelle version datée.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Les politiques de distribution sont conservées.")


class DistributionPolicyShare(models.Model):
    """Shareholders covered by a policy and, for fixed modes, their percentage (must total 100)."""
    policy = models.ForeignKey(DistributionPolicy, on_delete=models.CASCADE, related_name="shares")
    stakeholder = models.ForeignKey("stakeholders.Stakeholder", on_delete=models.RESTRICT, related_name="distribution_shares")
    percent = models.DecimalField(max_digits=7, decimal_places=4)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["policy", "stakeholder"], name="unique_policy_shareholder"),
            models.CheckConstraint(condition=models.Q(percent__gte=0, percent__lte=100), name="policy_share_percent_valid"),
        ]


class PartialMonthRule(models.TextChoices):
    PRORATA = "PRORATA", _("Prorata des jours")
    FULL = "FULL", _("Mois complet dû")
    NONE = "NONE", _("Rien n’est dû pour un mois incomplet")


class LossMonthRule(models.TextChoices):
    PAY = "PAY", _("Rémunération due même en mois déficitaire")
    SUSPEND = "SUSPEND", _("Rémunération suspendue en mois déficitaire")


class RemunerationTerms(models.Model):
    """Contractual parameters that the fixed monthly remuneration rule alone does not define."""
    stakeholder = models.OneToOneField("stakeholders.Stakeholder", on_delete=models.RESTRICT, related_name="remuneration_terms")
    contract_start = models.DateField(null=True, blank=True)
    contract_end = models.DateField(null=True, blank=True)
    partial_month = models.CharField(max_length=8, choices=PartialMonthRule.choices, blank=True)
    loss_month = models.CharField(max_length=8, choices=LossMonthRule.choices, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    updated_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT)
    updated_at = models.DateTimeField(auto_now=True)


class CurrencyConversion(models.Model):
    """A conversion keeps both amounts and the rate applied; the realised difference is isolated."""
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    client_key = models.CharField(max_length=120, unique=True)
    source = models.ForeignKey(FinancialAccount, on_delete=models.RESTRICT, related_name="conversions_out")
    destination = models.ForeignKey(FinancialAccount, on_delete=models.RESTRICT, related_name="conversions_in")
    amount_source = models.DecimalField(max_digits=18, decimal_places=2)
    amount_destination = models.DecimalField(max_digits=18, decimal_places=2)
    rate_applied = models.DecimalField(max_digits=18, decimal_places=6)
    reference_rate = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    fee_source = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    realized_difference = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    note = models.CharField(max_length=255, blank=True)
    batch = models.OneToOneField(JournalBatch, on_delete=models.RESTRICT, null=True, blank=True, related_name="conversion")
    created_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount_source__gt=0, amount_destination__gt=0, rate_applied__gt=0), name="conversion_amounts_positive"),
            models.CheckConstraint(condition=models.Q(fee_source__gte=0), name="conversion_fee_non_negative"),
        ]


class RequestKind(models.TextChoices):
    REINVEST_PROFIT = "REINVEST_PROFIT", _("Réinvestir un bénéfice")
    WITHDRAW_PROFIT = "WITHDRAW_PROFIT", _("Retirer un bénéfice")
    INCREASE_INVESTMENT = "INCREASE_INVESTMENT", _("Augmenter l’investissement")
    WITHDRAW_INVESTMENT = "WITHDRAW_INVESTMENT", _("Retirer l’investissement")
    INCREASE_GUARANTEE = "INCREASE_GUARANTEE", _("Augmenter la garantie")
    CONVERT_COMMISSION = "CONVERT_COMMISSION", _("Convertir des commissions en garantie")


class RequestStatus(models.TextChoices):
    SUBMITTED = "SUBMITTED", _("Soumise")
    APPROVED = "APPROVED", _("Validée")
    REJECTED = "REJECTED", _("Refusée")
    EXECUTED = "EXECUTED", _("Exécutée")


class StakeholderRequest(models.Model):
    """A request from a shareholder, investor or partner. Nothing moves before validation and execution."""
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    stakeholder = models.ForeignKey("stakeholders.Stakeholder", on_delete=models.RESTRICT, related_name="requests")
    kind = models.CharField(max_length=24, choices=RequestKind.choices)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.ForeignKey("pricing.Currency", on_delete=models.RESTRICT)
    distribution = models.ForeignKey("profits.Distribution", on_delete=models.RESTRICT, null=True, blank=True, related_name="requests")
    note = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=RequestStatus.choices, default=RequestStatus.SUBMITTED)
    submitted_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT, related_name="requests_submitted")
    submitted_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT, null=True, blank=True, related_name="requests_decided")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_comment = models.CharField(max_length=255, blank=True)
    executed_at = models.DateTimeField(null=True, blank=True)
    execution_batch = models.OneToOneField(JournalBatch, on_delete=models.RESTRICT, null=True, blank=True, related_name="executed_request")

    class Meta:
        ordering = ["-submitted_at"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="request_amount_positive")]
