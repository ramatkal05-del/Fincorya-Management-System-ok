from django.db import models
from django.utils.translation import gettext_lazy as _

class ProfitStatus(models.TextChoices):
    DRAFT = "DRAFT", _("Brouillon")
    FINALIZED = "FINALIZED", _("Finalisée")

class DistributionStatus(models.TextChoices):
    DUE = "DUE", _("Due")
    PAID = "PAID", _("Payée")
    REINVESTED = "REINVESTED", _("Réinvestie dans le capital")

class ProfitPeriod(models.Model):
    proposed_by = models.ForeignKey(blank=True, null=True, on_delete=models.RESTRICT, related_name='distribution_proposals', to="accounts.User")
    proposed_at = models.DateTimeField(blank=True, null=True)
    proposed_distribution = models.DecimalField(decimal_places=2, default=0, max_digits=18)
    finance_period = models.ForeignKey(blank=True, null=True, on_delete=models.RESTRICT, to='finance.financialperiod')
    start_date = models.DateField()
    end_date = models.DateField()
    currency = models.ForeignKey("pricing.Currency", on_delete=models.RESTRICT)
    gross_fees = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    deductible_expenses = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    net_profit = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    snapshot = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=ProfitStatus.choices, default=ProfitStatus.DRAFT)
    calculated_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True)
    calculated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(end_date__gte=models.F("start_date")), name="profit_period_dates_valid"),
            models.UniqueConstraint(fields=["start_date", "end_date", "currency"], name="unique_profit_period_currency"),
        ]

class Allocation(models.Model):
    period = models.ForeignKey(ProfitPeriod, on_delete=models.CASCADE, related_name="allocations")
    bucket = models.CharField(max_length=40)
    percentage = models.DecimalField(max_digits=5, decimal_places=2)
    amount = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["period", "bucket"], name="unique_period_bucket")]

class Distribution(models.Model):
    payment_batch = models.OneToOneField(blank=True, null=True, on_delete=models.RESTRICT, related_name='paid_distribution', to='finance.journalbatch')
    approval_batch = models.OneToOneField(blank=True, null=True, on_delete=models.RESTRICT, related_name='approved_distribution', to='finance.journalbatch')
    allocation = models.ForeignKey(Allocation, on_delete=models.RESTRICT, related_name="distributions")
    stakeholder = models.ForeignKey("stakeholders.Stakeholder", on_delete=models.RESTRICT, related_name="distributions")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    status = models.CharField(max_length=10, choices=DistributionStatus.choices, default=DistributionStatus.DUE)
    paid_at = models.DateTimeField(null=True, blank=True)
