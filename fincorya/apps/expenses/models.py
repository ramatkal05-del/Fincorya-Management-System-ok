import uuid
from django.db import models
from django.utils.translation import gettext_lazy as _

class ExpenseStatus(models.TextChoices):
    PENDING = "PENDING", _("En attente")
    APPROVED = "APPROVED", _("Approuvée")
    REJECTED = "REJECTED", _("Rejetée")

class ApprovalLevel(models.TextChoices):
    ACCOUNTANT = "ACCOUNTANT", _("Comptable")
    ADMIN = "ADMIN", _("Administrateur")

class ApprovalDecision(models.TextChoices):
    APPROVED = "APPROVED", _("Approuvée")
    REJECTED = "REJECTED", _("Rejetée")

class ExpenseCategory(models.TextChoices):
    GENERAL = "GENERAL", _("Dépense générale")
    SALARY = "SALARY", _("Salaire d'agent")
    RENT = "RENT", _("Loyer")
    OPERATIONS = "OPERATIONS", _("Charge opérationnelle")
    SUPPLIER_FEE = "SUPPLIER_FEE", _("Frais fournisseur")
    INVESTOR_RETURN = "INVESTOR_RETURN", _("Rémunération investisseur")
    OTHER = "OTHER", _("Autre")

class Expense(models.Model):
    stakeholder = models.ForeignKey(blank=True, null=True, on_delete=models.RESTRICT, to='stakeholders.stakeholder')
    recognition_batch = models.OneToOneField(blank=True, null=True, on_delete=models.RESTRICT, related_name='recognized_expense', to='finance.journalbatch')
    accrual_key = models.CharField(blank=True, editable=False, max_length=120, null=True, unique=True)
    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    label = models.CharField(max_length=180)
    category = models.CharField(max_length=16, choices=ExpenseCategory.choices, default=ExpenseCategory.GENERAL)
    agent = models.ForeignKey("accounts.User", on_delete=models.RESTRICT, null=True, blank=True, related_name="salary_expenses")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    currency = models.ForeignKey("pricing.Currency", on_delete=models.RESTRICT)
    incurred_on = models.DateField()
    receipt = models.FileField(upload_to="private/expenses/%Y/%m/", blank=True)
    status = models.CharField(max_length=10, choices=ExpenseStatus.choices, default=ExpenseStatus.PENDING)
    created_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT, related_name="expenses_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-incurred_on", "-created_at"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="expense_amount_positive")]
        indexes = [models.Index(fields=["status", "-incurred_on"], name="expense_status_date_idx")]

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.category == ExpenseCategory.SALARY and not self.agent_id:
            raise ValidationError({"agent": "Sélectionnez l'agent concerné par ce salaire."})

class ExpenseApproval(models.Model):
    expense = models.ForeignKey(Expense, on_delete=models.CASCADE, related_name="approvals")
    level = models.CharField(max_length=12, choices=ApprovalLevel.choices)
    decision = models.CharField(max_length=10, choices=ApprovalDecision.choices)
    decided_by = models.ForeignKey("accounts.User", on_delete=models.RESTRICT)
    comment = models.TextField(blank=True)
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["expense", "level"], name="one_expense_decision_per_level")]

class ExpensePayment(models.Model):
    amount = models.DecimalField(decimal_places=2, max_digits=18)
    paid_at = models.DateTimeField()
    batch = models.OneToOneField(on_delete=models.RESTRICT, to='finance.journalbatch')
    expense = models.ForeignKey(on_delete=models.RESTRICT, related_name='payments', to='expenses.expense')
