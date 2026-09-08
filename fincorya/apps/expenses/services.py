from django.core.exceptions import PermissionDenied, ValidationError


from apps.finance.locking import ledger_atomic
from apps.accounts.models import Role
from apps.audit.services import record
from .models import ApprovalDecision, ApprovalLevel, Expense, ExpenseApproval, ExpenseStatus

@ledger_atomic
def decide_expense(*, expense_id, actor, decision, comment=""):
    expense = Expense.objects.select_for_update().get(pk=expense_id)
    if actor.role != Role.ADMIN:
        raise PermissionDenied("Rôle non autorisé à valider une charge.")
    if expense.status != ExpenseStatus.PENDING:
        raise ValidationError("Seule une charge en attente peut recevoir une décision.")
    level = ApprovalLevel.ADMIN
    if expense.approvals.filter(level=level).exists():
        raise ValidationError("Une décision existe déjà pour ce niveau.")
    approval = ExpenseApproval.objects.create(expense=expense, level=level, decision=decision, decided_by=actor, comment=comment)
    if decision == ApprovalDecision.REJECTED:
        expense.status = ExpenseStatus.REJECTED
    else:
        expense.status = ExpenseStatus.APPROVED
    expense.save(update_fields=["status"])
    record(actor=actor, action="EXPENSE_DECIDE", instance=expense, after={"level": level, "decision": decision})
    if expense.status == ExpenseStatus.APPROVED:
        from apps.finance.events import recognize_expense
        recognize_expense(expense, actor)
    return approval
