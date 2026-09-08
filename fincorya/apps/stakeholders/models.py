import uuid
from django.db import models
from django.utils.translation import gettext_lazy as _

class StakeholderType(models.TextChoices):
    PARTNER = "PARTNER", _("Partenaire")
    INVESTOR = "INVESTOR", _("Investisseur")
    SHAREHOLDER = "SHAREHOLDER", _("Actionnaire")
    STAFF = "STAFF", _("Personnel")


class PaymentFrequency(models.TextChoices):
    MONTHLY = "MONTHLY", _("Mensuelle")
    QUARTERLY = "QUARTERLY", _("Trimestrielle")
    SEMIANNUAL = "SEMIANNUAL", _("Semestrielle")
    ANNUAL = "ANNUAL", _("Annuelle")
    AT_MATURITY = "AT_MATURITY", _("À l'échéance")

class Stakeholder(models.Model):
    canonical_identity = models.CharField(blank=True, editable=False, max_length=80, null=True, unique=True)
    started_on = models.DateField(blank=True, null=True)
    phone = models.CharField(blank=True, max_length=30)
    notes = models.TextField(blank=True)
    ended_on = models.DateField(blank=True, null=True)
    address = models.TextField(blank=True)
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=180)
    type = models.CharField(max_length=12, choices=StakeholderType.choices)
    owner = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="owned_stakeholders")
    email = models.EmailField(blank=True)
    investor_return_percent = models.DecimalField(max_digits=7, decimal_places=2, default=0, verbose_name=_("Rendement convenu (%)"))
    payment_frequency = models.CharField(max_length=16, choices=PaymentFrequency.choices, default=PaymentFrequency.AT_MATURITY, verbose_name=_("Fréquence de paiement"))
    share_count = models.DecimalField(max_digits=18, decimal_places=4, default=0, verbose_name=_("Nombre d'actions"))
    share_unit_value = models.DecimalField(max_digits=18, decimal_places=2, default=0, verbose_name=_("Valeur unitaire de l'action"))
    dividend_percent = models.DecimalField(max_digits=7, decimal_places=2, default=0, verbose_name=_("Pourcentage de dividende (%)"))
    partner_share_percent = models.DecimalField(max_digits=7, decimal_places=2, default=40, verbose_name=_("Part partenaire (%)"))
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner"], condition=models.Q(owner__isnull=False), name="unique_stakeholder_owner"),
            models.CheckConstraint(condition=models.Q(investor_return_percent__gte=0), name="stakeholder_return_non_negative"),
            models.CheckConstraint(condition=models.Q(share_count__gte=0), name="stakeholder_shares_non_negative"),
            models.CheckConstraint(condition=models.Q(share_unit_value__gte=0), name="stakeholder_share_value_non_negative"),
            models.CheckConstraint(condition=models.Q(dividend_percent__gte=0, dividend_percent__lte=100), name="stakeholder_dividend_valid"),
            models.CheckConstraint(condition=models.Q(partner_share_percent__gte=0, partner_share_percent__lte=100), name="stakeholder_partner_share_valid"),
        ]

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        if ' '.join(self.name.split()).casefold() in {'jenovic mpoto', 'mpoto jenovic'}:
            self.name = 'Mpoto Jenovic'
            self.canonical_identity = 'mpoto-jenovic'
            if type(self).objects.filter(canonical_identity=self.canonical_identity).exclude(pk=self.pk).exists():
                raise ValidationError('Cette identité existe déjà.')
            if kwargs.get('update_fields') is not None:
                kwargs['update_fields'] = set(kwargs['update_fields']) | {'name', 'canonical_identity'}
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"

class Investment(models.Model):
    stakeholder = models.ForeignKey(Stakeholder, on_delete=models.RESTRICT, related_name="investments")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.ForeignKey("pricing.Currency", on_delete=models.RESTRICT)
    invested_on = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="investment_amount_positive")]

class PartnerOperation(models.Model):
    stakeholder = models.ForeignKey(Stakeholder, on_delete=models.RESTRICT, related_name="partner_operations")
    operation = models.OneToOneField("operations.Operation", on_delete=models.RESTRICT, related_name="partner_attribution")
    share_percent = models.DecimalField(max_digits=5, decimal_places=2, default=40)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(share_percent__gte=0, share_percent__lte=100), name="partner_share_valid")]

    def save(self, *args, **kwargs):
        if self._state.adding and self.share_percent == 40 and self.stakeholder_id:
            self.share_percent = self.stakeholder.partner_share_percent
        super().save(*args, **kwargs)

class EconomicRole(models.TextChoices):
    SHAREHOLDER = "SHAREHOLDER", _("Actionnaire")
    INVESTOR = "INVESTOR", _("Investisseur")
    PARTNER = "PARTNER", _("Partenaire / associé")
    AGENT = "AGENT", _("Agent")
    OPERATOR = "OPERATOR", _("Opérateur / opératrice")

class StakeholderRole(models.Model):
    role = models.CharField(choices=EconomicRole.choices, max_length=16)
    effective_from = models.DateField()
    effective_to = models.DateField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    stakeholder = models.ForeignKey(on_delete=models.RESTRICT, related_name='economic_roles', to='stakeholders.stakeholder')

    class Meta:
        constraints = [models.UniqueConstraint(fields=('stakeholder', 'role', 'effective_from'), name='unique_party_role_effective_date'), models.CheckConstraint(condition=models.Q(('effective_to__isnull', True), ('effective_to__gte', models.F('effective_from')), _connector='OR'), name='party_role_dates_valid')]
