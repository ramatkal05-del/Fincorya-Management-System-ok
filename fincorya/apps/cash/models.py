"""
FINCORYA cash domain. The balance is never written directly
by a view or by `Model.save()`. Every change to `CashAccount.balance` goes
through `cash.services` inside `transaction.atomic()` with
`select_for_update()`, and is paired with an immutable `CashMovement` row —
this is what section 11 of the plan calls "verrouillage du compte de caisse
pendant le mouvement".
"""
from django.db import models
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


class GlobalCashAccount(models.Model):
    """Administrator reserve that finances every agent cash account in a currency."""
    administrator = models.ForeignKey("accounts.User", on_delete=models.RESTRICT, related_name="global_cash_accounts")
    currency = models.OneToOneField("pricing.Currency", on_delete=models.RESTRICT, related_name="global_cash_account")
    balance = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Caisse globale")
        verbose_name_plural = _("Caisses globales")
        constraints = [models.CheckConstraint(condition=models.Q(balance__gte=0), name="global_cash_balance_non_negative")]

    @property
    def capital(self):
        allocated = self.agent_accounts.aggregate(total=models.Sum("balance"))["total"] or 0
        return self.balance + allocated

    def __str__(self):
        return f"Caisse globale {self.currency.code} - réserve {self.balance}"

    def save(self, *args, **kwargs):
        ledger_write = getattr(self, "_ledger_write", False)
        if not ledger_write:
            if self._state.adding and self.balance:
                raise ValidationError(_("Le capital initial doit être enregistré par un mouvement global."))
            if not self._state.adding:
                previous = type(self).objects.only("balance").get(pk=self.pk)
                if previous.balance != self.balance:
                    raise ValidationError(_("Le solde global ne peut être modifié que par le grand livre."))
        return super().save(*args, **kwargs)


class CashAccount(models.Model):
    """One FINCORYA cash account per agent/currency pair."""
    agent = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="cash_accounts")
    currency = models.ForeignKey("pricing.Currency", on_delete=models.RESTRICT, related_name="cash_accounts")
    global_account = models.ForeignKey(
        GlobalCashAccount, on_delete=models.RESTRICT, null=True, blank=True,
        related_name="agent_accounts", verbose_name=_("Caisse globale source"),
    )
    balance = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    is_active = models.BooleanField(default=False, verbose_name=_("Allouée et active"))
    allocated_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="cash_accounts_allocated", verbose_name=_("Allouée par"),
    )
    allocated_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Allouée le"))
    is_locked = models.BooleanField(default=False, verbose_name=_("Verrouillé (clôture en cours)"))

    class Meta:
        verbose_name = _("Compte de caisse")
        verbose_name_plural = _("Comptes de caisse")
        constraints = [
            models.UniqueConstraint(fields=["agent", "currency"], name="one_account_per_agent_currency"),
            models.CheckConstraint(condition=models.Q(balance__gte=0), name="cash_balance_non_negative"),
        ]

    def __str__(self):
        return f"Caisse {self.agent} - {self.balance} {self.currency.code}"

    def clean(self):
        super().clean()
        if self.global_account_id and self.currency_id != self.global_account.currency_id:
            raise ValidationError(_("La caisse globale et la caisse agent doivent utiliser la même devise."))

    def save(self, *args, **kwargs):
        ledger_write = getattr(self, "_ledger_write", False)
        if not ledger_write:
            if self._state.adding and self.balance:
                raise ValidationError(_("Le solde initial doit être enregistré par un mouvement de caisse."))
            if not self._state.adding:
                previous = type(self).objects.only("balance").get(pk=self.pk)
                if previous.balance != self.balance:
                    raise ValidationError(_("Le solde de caisse ne peut être modifié que par le grand livre."))
        return super().save(*args, **kwargs)


class CashFunding(models.Model):
    """Auditable administrator allocation or replenishment of an agent cash account."""
    account = models.ForeignKey(CashAccount, on_delete=models.RESTRICT, related_name="fundings")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    note = models.CharField(max_length=255, blank=True)
    allocated_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT, related_name="cash_fundings")
    applied_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="cash_funding_amount_positive")]

    def __str__(self):
        return f"{self.account} + {self.amount}"


class MovementDirection(models.TextChoices):
    IN = "IN", _("Entrée")
    OUT = "OUT", _("Sortie")


class GlobalMovementType(models.TextChoices):
    INITIAL = "INITIAL", _("Solde initial migré")
    CAPITAL_IN = "CAPITAL_IN", _("Apport de capital")
    CAPITAL_OUT = "CAPITAL_OUT", _("Retrait de capital")
    ALLOCATION = "ALLOCATION", _("Allocation à une caisse agent")
    HANDOVER = "HANDOVER", _("Remise d'une caisse agent")


class MovementType(models.TextChoices):
    OPERATION = "OPERATION", _("Opération")
    HANDOVER = "HANDOVER", _("Remise")
    ADJUSTMENT = "ADJUSTMENT", _("Ajustement")
    OPENING = "OPENING", _("Ouverture de caisse")


class ImmutableMovementQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError(_("Le grand livre de caisse est immuable."))

    def delete(self):
        raise ValidationError(_("Le grand livre de caisse ne peut pas être supprimé."))


class GlobalCashMovement(models.Model):
    """Append-only ledger for every change to the administrator reserve."""
    global_account = models.ForeignKey(GlobalCashAccount, on_delete=models.RESTRICT, related_name="movements")
    direction = models.CharField(max_length=3, choices=MovementDirection.choices)
    movement_type = models.CharField(max_length=20, choices=GlobalMovementType.choices)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    balance_after = models.DecimalField(max_digits=18, decimal_places=2)
    funding = models.OneToOneField(
        CashFunding, on_delete=models.RESTRICT, null=True, blank=True, related_name="global_movement"
    )
    handover = models.OneToOneField(
        "CashHandover", on_delete=models.RESTRICT, null=True, blank=True, related_name="global_movement"
    )
    note = models.CharField(max_length=255)
    created_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT, related_name="global_cash_movements")
    created_at = models.DateTimeField(auto_now_add=True)
    objects = ImmutableMovementQuerySet.as_manager()

    class Meta:
        verbose_name = _("Mouvement de caisse globale")
        verbose_name_plural = _("Mouvements de caisse globale")
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="global_cash_movement_amount_positive"),
            models.CheckConstraint(condition=models.Q(balance_after__gte=0), name="global_cash_movement_balance_non_negative"),
        ]
        indexes = [models.Index(fields=["global_account", "-created_at"], name="global_cash_date_idx")]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(_("Un mouvement de caisse globale est immuable."))
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(_("Un mouvement de caisse globale ne peut pas être supprimé."))

    def __str__(self):
        sign = "+" if self.direction == MovementDirection.IN else "-"
        return f"{sign}{self.amount} {self.global_account.currency.code}"


class CashMovement(models.Model):
    """Append-only ledger line. Never updated or deleted — corrections are counter-movements."""
    account = models.ForeignKey(CashAccount, on_delete=models.RESTRICT, related_name="movements")
    direction = models.CharField(max_length=3, choices=MovementDirection.choices)
    movement_type = models.CharField(max_length=20, choices=MovementType.choices)
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    balance_after = models.DecimalField(max_digits=16, decimal_places=2)
    operation = models.ForeignKey(
        "operations.Operation", on_delete=models.SET_NULL, null=True, blank=True, related_name="movements"
    )
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = ImmutableMovementQuerySet.as_manager()

    class Meta:
        verbose_name = _("Mouvement de caisse")
        verbose_name_plural = _("Mouvements de caisse")
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="cash_movement_amount_positive"),
            models.UniqueConstraint(fields=["operation"], condition=models.Q(operation__isnull=False, movement_type=MovementType.OPERATION), name="one_primary_movement_per_operation"),
        ]
        indexes = [models.Index(fields=["account", "-created_at"], name="cash_account_date_idx")]

    def __str__(self):
        sign = "+" if self.direction == MovementDirection.IN else "-"
        return f"{sign}{self.amount} sur {self.account}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(_("Un mouvement de caisse est immuable."))
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(_("Un mouvement de caisse ne peut pas être supprimé."))


class HandoverStatus(models.TextChoices):
    PENDING = "PENDING", _("En attente")
    CONFIRMED = "CONFIRMED", _("Confirmée")
    REJECTED = "REJECTED", _("Rejetée")


class CashHandover(models.Model):
    """Cash physically handed from an agent up to a supervisor, needs supervisor confirmation."""
    account = models.ForeignKey(CashAccount, on_delete=models.RESTRICT, related_name="handovers")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    requested_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT, related_name="handovers_requested")
    confirmed_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="handovers_confirmed"
    )
    status = models.CharField(max_length=10, choices=HandoverStatus.choices, default=HandoverStatus.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Remise de caisse")
        verbose_name_plural = _("Remises de caisse")
        ordering = ["-created_at"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="handover_amount_positive")]


class ClosureStatus(models.TextChoices):
    OPEN = "OPEN", _("Ouverte")
    CLOSED = "CLOSED", _("Clôturée")


class DailyClosure(models.Model):
    """Daily reconciliation keyed by FINCORYA business date."""
    account = models.ForeignKey(CashAccount, on_delete=models.RESTRICT, related_name="closures")
    business_date = models.DateField(verbose_name=_("Journée métier (Europe/Istanbul)"))
    opening_balance = models.DecimalField(max_digits=16, decimal_places=2)
    expected_balance = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    declared_cash = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    variance = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    justification = models.TextField(blank=True, verbose_name=_("Justification de l'écart"))
    status = models.CharField(max_length=10, choices=ClosureStatus.choices, default=ClosureStatus.OPEN)
    closed_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="closures_performed"
    )
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Clôture quotidienne")
        verbose_name_plural = _("Clôtures quotidiennes")
        ordering = ["-business_date"]
        constraints = [
            models.UniqueConstraint(fields=["account", "business_date"], name="one_closure_per_account_per_day"),
        ]

    def __str__(self):
        return f"Clôture {self.account} - {self.business_date}"
