from decimal import Decimal, InvalidOperation
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.accounts.views import mfa_required
from apps.cash.models import CashAccount
from apps.pricing.models import TariffSchedule
from apps.pricing.services import current_rate_to_usd, from_usd, lookup_fee
from .forms import OperationCancellationForm, OperationFilterForm, OperationForm, OperationRevisionForm
from .models import Operation, OperationStatus, OperationType
from .services import cancel_operation, create_sent_transfer, create_withdrawal, pay_received_transfer, receive_transfer, revise_operation


def _operations_for(user):
    rows = Operation.objects.select_related("agent", "currency", "account")
    if user.role == Role.AGENT:
        return rows.filter(agent=user)
    if user.role == Role.ADMIN:
        return rows
    return rows.none()


@mfa_required
def operation_list(request):
    form = OperationFilterForm(request.GET)
    rows = _operations_for(request.user)
    if form.is_valid():
        if form.cleaned_data["q"]:
            rows = rows.filter(
                Q(reference__icontains=form.cleaned_data["q"])
                | Q(note__icontains=form.cleaned_data["q"])
                | Q(customer_name__icontains=form.cleaned_data["q"])
                | Q(customer_identifier__icontains=form.cleaned_data["q"])
                | Q(service__icontains=form.cleaned_data["q"])
            )
        if form.cleaned_data["type"]:
            rows = rows.filter(type=form.cleaned_data["type"])
        if form.cleaned_data["service"]:
            rows = rows.filter(service=form.cleaned_data["service"])
        if form.cleaned_data["status"]:
            rows = rows.filter(status=form.cleaned_data["status"])
    page = Paginator(rows, 25).get_page(request.GET.get("page"))
    template = "operations/_table.html" if request.htmx else "operations/list.html"
    return render(request, template, {"filter_form": form, "page": page})


@mfa_required
def operation_create(request):
    if request.user.role not in {Role.AGENT, Role.ADMIN}:
        raise PermissionDenied
    form = OperationForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        service_map = {
            OperationType.SENT_TRANSFER: create_sent_transfer,
            OperationType.WITHDRAWAL: create_withdrawal,
        }
        service = service_map.get(data["type"])
        if service is None:
            form.add_error("type", "Ce type d'opération n'est pas pris en charge ici.")
        else:
            try:
                operation = service(
                    agent=request.user, account_id=data["account"].pk, amount=data["amount"],
                    tariff_schedule=data["tariff_schedule"], manual_fee=None,
                    fee_justification="", note=data["note"],
                    idempotency_key=data["idempotency_key"],
                    service=data["service"], customer_identifier=data["customer_identifier"],
                    customer_name=data["customer_name"],
                )
            except (ValidationError, PermissionDenied) as exc:
                form.add_error(None, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
            else:
                messages.success(request, f"Opération {operation.reference} enregistrée.")
                return redirect("operations:detail", reference=operation.reference)
    return render(request, "operations/form.html", {"form": form})


@mfa_required
def operation_preview(request):
    try:
        account = CashAccount.objects.select_related("currency", "agent").get(pk=request.GET.get("account"))
        schedule = TariffSchedule.objects.get(pk=request.GET.get("tariff_schedule"), is_published=True, currency__code="USD")
        amount = Decimal(request.GET.get("amount", "0"))
        if amount <= 0:
            raise ValueError
        # Object authorization is enforced before any financial information is disclosed.
        allowed = account.agent_id == request.user.pk if request.user.role == Role.AGENT else request.user.role == Role.ADMIN
        if not allowed:
            raise PermissionDenied
        rate = current_rate_to_usd(account.currency)
        amount_usd = (amount * rate).quantize(Decimal("0.01"))
        fee_usd = lookup_fee(schedule, amount_usd) if amount_usd <= Decimal("5000") else None
        context = {"amount": amount, "currency": account.currency.code, "amount_usd": amount_usd, "rate": rate, "fee_usd": fee_usd, "fee_local": from_usd(fee_usd, account.currency) if fee_usd is not None else None}
        return render(request, "operations/_preview.html", context)
    except (CashAccount.DoesNotExist, TariffSchedule.DoesNotExist, InvalidOperation, ValueError, ValidationError, PermissionDenied) as exc:
        message = "; ".join(exc.messages) if isinstance(exc, ValidationError) else "Complétez le montant, la caisse et la grille pour afficher l'impact."
        return HttpResponse(message, status=422)


@mfa_required
def operation_detail(request, reference):
    operation = get_object_or_404(_operations_for(request.user), reference=reference)
    return render(request, "operations/detail.html", {
        "operation": operation,
        "cancellation_form": OperationCancellationForm(),
        "revision_form": OperationRevisionForm(initial={"amount": operation.amount, "fee": operation.fee, "note": operation.note}),
    })


@require_POST
@mfa_required
def operation_cancel(request, reference):
    operation = get_object_or_404(_operations_for(request.user), reference=reference)
    form = OperationCancellationForm(request.POST)
    if form.is_valid():
        try:
            cancel_operation(operation_id=operation.pk, cancelled_by=request.user, **form.cleaned_data)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
        else:
            messages.success(request, "Opération annulée avec écriture compensatoire.")
    else:
        messages.error(request, "Le motif d'annulation est invalide.")
    return redirect("operations:detail", reference=reference)


@require_POST
@mfa_required
def operation_revise(request, reference):
    operation = get_object_or_404(_operations_for(request.user), reference=reference)
    form = OperationRevisionForm(request.POST)
    if form.is_valid():
        data = form.cleaned_data
        try:
            revise_operation(
                operation_id=operation.pk,
                changes={"amount": data["amount"], "fee": data["fee"], "note": data["note"]},
                reason=data["reason"], revised_by=request.user,
            )
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
        else:
            messages.success(request, "Opération corrigée avec traçabilité complète.")
    else:
        messages.error(request, "Les données de correction sont invalides.")
    return redirect("operations:detail", reference=reference)


@require_POST
@mfa_required
def operation_pay(request, reference):
    operation = get_object_or_404(_operations_for(request.user), reference=reference, status=OperationStatus.PENDING, type=OperationType.RECEIVED_TRANSFER)
    try:
        pay_received_transfer(operation_id=operation.pk, paid_by=request.user)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Paiement enregistré une seule fois et caisse mise à jour.")
    return redirect("operations:detail", reference=reference)
