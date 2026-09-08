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
