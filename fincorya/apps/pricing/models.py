"""
FINCORYA pricing domain. Guarantees required by the MVP plan:
  * Rates and tariffs are versioned instead of mutated in place, so a past
    operation's `taux_conversion` / `frais_calcules` always trace back to the
    rate that was actually applied.
  * Tiers are constrained to be non-overlapping (validated in service layer,
    enforced additionally by a DB constraint).
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils import timezone

ALLOWED_CURRENCY_CODES = ["USD", "EUR", "TRY", "GBP", "CDF"]


class Currency(models.Model):
    code = models.CharField(max_length=3, unique=True, choices=[(c, c) for c in ALLOWED_CURRENCY_CODES])
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _("Devise")
        verbose_name_plural = _("Devises")

    def __str__(self):
        return self.code

    def current_rate(self):
        if self.code == "USD":
            return ExchangeRate(currency=self, rate_to_usd=Decimal("1.000000"), effective_at=timezone.now())
        return self.rates.filter(effective_at__lte=timezone.now()).order_by("-effective_at").first()


class ExchangeRate(models.Model):
    """Versioned rate to USD, FINCORYA's reference currency."""
    currency = models.ForeignKey(Currency, on_delete=models.CASCADE, related_name="rates")
    rate_to_usd = models.DecimalField(max_digits=14, decimal_places=6, verbose_name=_("Taux vers USD"))
    effective_at = models.DateTimeField(verbose_name=_("En vigueur depuis"))
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Taux de change")
        verbose_name_plural = _("Taux de change")
        ordering = ["-effective_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(rate_to_usd__gt=0), name="exchange_rate_positive"),
            models.UniqueConstraint(fields=["currency", "effective_at"], name="unique_currency_rate_effective_at"),
        ]

    def __str__(self):
        return f"{self.currency.code} -> USD @ {self.rate_to_usd} ({self.effective_at:%Y-%m-%d})"


class TariffSchedule(models.Model):
    """A named FINCORYA tariff grid, such as a corridor or zone."""
    name = models.CharField(max_length=80, unique=True, verbose_name=_("Nom de la grille"))
    currency = models.ForeignKey(Currency, on_delete=models.RESTRICT, related_name="tariff_schedules")
    is_published = models.BooleanField(default=False, verbose_name=_("Publiée"))

    class Meta:
        verbose_name = _("Grille tarifaire")
        verbose_name_plural = _("Grilles tarifaires")

    def __str__(self):
        return self.name


class TariffTierQuerySet(models.QuerySet):
    def bulk_create(self, objs, **kwargs):
        objs = list(objs)
        for index, obj in enumerate(objs):
            obj.full_clean()
            for other in objs[index + 1:]:
                if obj.schedule_id == other.schedule_id and obj.min_amount <= other.max_amount and obj.max_amount >= other.min_amount:
                    raise ValidationError(_("Les tranches importées ne peuvent pas se chevaucher."))
        return super().bulk_create(objs, **kwargs)


class TariffTier(models.Model):
    """One amount bracket within a FINCORYA tariff schedule."""
    schedule = models.ForeignKey(TariffSchedule, on_delete=models.CASCADE, related_name="tiers")
    min_amount = models.DecimalField(max_digits=14, decimal_places=2, verbose_name=_("Montant minimum"))
    max_amount = models.DecimalField(max_digits=14, decimal_places=2, verbose_name=_("Montant maximum"))
    fixed_fee = models.DecimalField(max_digits=12, decimal_places=2, verbose_name=_("Frais fixe"))
    objects = TariffTierQuerySet.as_manager()

    class Meta:
        verbose_name = _("Tranche tarifaire")
        verbose_name_plural = _("Tranches tarifaires")
        ordering = ["schedule", "min_amount"]
        constraints = [
            models.CheckConstraint(check=models.Q(max_amount__gte=models.F("min_amount")), name="tier_max_gte_min"),
            models.CheckConstraint(condition=models.Q(min_amount__gt=0), name="tier_min_positive"),
            models.CheckConstraint(condition=models.Q(fixed_fee__gte=0), name="tier_fee_non_negative"),
        ]

    def clean(self):
        overlapping = TariffTier.objects.filter(
            schedule=self.schedule,
            min_amount__lte=self.max_amount,
            max_amount__gte=self.min_amount,
        ).exclude(pk=self.pk)
        if overlapping.exists():
            raise ValidationError(_("Cette tranche chevauche une tranche existante de la même grille."))

    def __str__(self):
        return f"{self.schedule.name}: {self.min_amount}-{self.max_amount} -> {self.fixed_fee}"

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
