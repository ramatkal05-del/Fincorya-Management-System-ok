from pathlib import Path

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.accounts.views import mfa_required
from apps.audit.services import record
from .forms import ExpenseDecisionForm, ExpenseForm
from .models import Expense, ExpenseStatus
from .services import decide_expense


ALLOWED_ROLES = {Role.ADMIN}


def _ensure_access(user):
    if user.role not in ALLOWED_ROLES:
        raise PermissionDenied("Accès réservé au contrôle financier.")


@mfa_required
def expense_list(request):
    _ensure_access(request.user)
    rows = Expense.objects.select_related("currency", "created_by").prefetch_related("approvals")
    return render(request, "expenses/list.html", {"expenses": rows})


@mfa_required
def expense_create(request):
    if request.user.role != Role.ADMIN:
        raise PermissionDenied("Vous ne pouvez pas enregistrer une charge.")
    form = ExpenseForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        expense = form.save(commit=False)
        expense.created_by = request.user
        expense.status = ExpenseStatus.PENDING
        expense.full_clean()
        expense.save()
        record(actor=request.user, action="EXPENSE_CREATE", instance=expense, after={"amount": str(expense.amount), "currency": expense.currency.code})
        messages.success(request, "Dépense ou charge enregistrée et envoyée pour validation.")
        return redirect("expenses:detail", expense_id=expense.pk)
    return render(request, "expenses/form.html", {"form": form})


@mfa_required
def expense_detail(request, expense_id):
    _ensure_access(request.user)
    expense = get_object_or_404(Expense.objects.select_related("currency", "created_by").prefetch_related("approvals__decided_by"), pk=expense_id)
    return render(request, "expenses/detail.html", {"expense": expense, "decision_form": ExpenseDecisionForm()})


@require_POST
@mfa_required
def expense_decide(request, expense_id):
    _ensure_access(request.user)
    form = ExpenseDecisionForm(request.POST)
    if form.is_valid():
        try:
            decide_expense(expense_id=expense_id, actor=request.user, **form.cleaned_data)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
        else:
            messages.success(request, "Décision enregistrée dans la piste d’audit.")
    else:
        messages.error(request, "Décision invalide.")
    return redirect("expenses:detail", expense_id=expense_id)


@mfa_required
def receipt_download(request, expense_id):
    _ensure_access(request.user)
    expense = get_object_or_404(Expense, pk=expense_id)
    if not expense.receipt:
        raise Http404("Aucun justificatif.")
    try:
        handle = expense.receipt.open("rb")
    except OSError as exc:
        raise Http404("Fichier indisponible.") from exc
    return FileResponse(handle, as_attachment=True, filename=Path(expense.receipt.name).name)
