from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.accounts.views import mfa_required
from .forms import ClosureForm, HandoverForm
from .models import CashAccount, CashHandover, GlobalCashAccount, HandoverStatus
from .services import close_cash_day, confirm_handover, handover_cash


def _accounts_for(user):
    rows = CashAccount.objects.select_related("agent", "currency", "global_account")
    if user.role == Role.AGENT:
        return rows.filter(agent=user, is_active=True)
    if user.role in {Role.ADMIN, Role.FINANCE_MANAGER}:
        return rows
    return rows.none()


@mfa_required
def cash_list(request):
    global_accounts = GlobalCashAccount.objects.select_related("currency", "administrator") if request.user.role in {Role.ADMIN, Role.FINANCE_MANAGER} else GlobalCashAccount.objects.none()
    return render(request, "cash/list.html", {"accounts": _accounts_for(request.user), "global_accounts": global_accounts})


@mfa_required
def cash_detail(request, account_id):
    account = get_object_or_404(_accounts_for(request.user), pk=account_id)
    return render(request, "cash/detail.html", {
        "account": account, "movements": account.movements.select_related("operation")[:50],
    })


@require_POST
@mfa_required
def handover_create(request, account_id):
    account = get_object_or_404(_accounts_for(request.user), pk=account_id)
    form = HandoverForm(request.POST)
    if form.is_valid():
        try:
            handover_cash(account_id=account.pk, amount=form.cleaned_data["amount"], requested_by=request.user)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
        else:
            messages.success(request, "Remise créée et envoyée à l'administrateur pour confirmation.")
    else:
        messages.error(request, "Le montant de la remise est invalide.")
    return redirect("cash:detail", account_id=account.pk)


@require_POST
@mfa_required
def handover_confirm(request, handover_id):
    handover = get_object_or_404(CashHandover, pk=handover_id, status=HandoverStatus.PENDING)
    try:
        confirm_handover(handover_id=handover.pk, confirmed_by=request.user)
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
    else:
        messages.success(request, "Remise confirmée et mouvement de caisse enregistré.")
    return redirect("cash:detail", account_id=handover.account_id)


@require_POST
@mfa_required
def closure_create(request, account_id):
    account = get_object_or_404(_accounts_for(request.user), pk=account_id)
    form = ClosureForm(request.POST)
    if form.is_valid():
        try:
            close_cash_day(account_id=account.pk, closed_by=request.user, **form.cleaned_data)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
        else:
            messages.success(request, "Journée clôturée et ouverture suivante préparée.")
    else:
        messages.error(request, "Corrigez les informations de clôture.")
    return redirect("cash:detail", account_id=account.pk)


@mfa_required
def closure_preview(request, account_id):
    account = get_object_or_404(_accounts_for(request.user), pk=account_id)
    try:
        declared = ClosureForm.base_fields["declared_cash"].clean(request.GET.get("declared_cash", ""))
    except ValidationError:
        declared = None
    return render(request, "cash/_closure_preview.html", {"account": account, "declared": declared, "variance": declared - account.balance if declared is not None else None})
