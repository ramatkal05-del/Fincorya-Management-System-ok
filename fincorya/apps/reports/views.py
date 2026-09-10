from pathlib import Path

from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.models import Role
from apps.accounts.views import mfa_required
from .forms import FinanceReportForm, MonthlyReportForm, OperationReportForm
from .models import ReportExport
from .services import generate_finance_report, generate_monthly_report, generate_operation_report


REPORT_ROLES = {Role.ADMIN, Role.AGENT}


@mfa_required
def report_center(request):
    """One entry point for every role; kinds, periods and scope are decided server-side."""
    from apps.finance.reporting import allowed_kinds, build_report, can_download
    if not allowed_kinds(request.user):
        raise PermissionDenied("Aucun rapport n’est disponible pour votre rôle.")
    form = FinanceReportForm(request.GET or None, user=request.user)
    report, error = None, None
    if request.GET and form.is_valid():
        data = form.cleaned_data
        try:
            if data["format"] == "HTML":
                report = build_report(user=request.user, kind=data["kind"], preset=data["preset"], anchor=data["anchor"], custom_end=data.get("custom_end"), filters=form.filters())
            else:
                export = generate_finance_report(user=request.user, kind=data["kind"], preset=data["preset"], anchor=data["anchor"], custom_end=data.get("custom_end"), format=data["format"], filters=form.filters())
                return redirect("reports:download", public_id=export.public_id)
        except (PermissionDenied, ValueError) as exc:
            error = str(exc)
        except Exception as exc:  # ValidationError from period parsing
            error = "; ".join(getattr(exc, "messages", [str(exc)]))
    exports = ReportExport.objects.filter(requested_by=request.user, kind__startswith="FINANCE_").order_by("-created_at")[:30]
    template = "reports/_center_result.html" if request.htmx else "reports/center.html"
    return render(request, template, {"form": form, "report": report, "error": error, "exports": exports, "can_download": can_download(request.user)})


@mfa_required
def report_list(request):
    if request.user.role not in REPORT_ROLES:
        raise PermissionDenied("Accès aux rapports refusé.")
    exports = ReportExport.objects.filter(requested_by=request.user).order_by("-created_at")[:50]
    monthly = request.user.role == Role.ADMIN and request.POST.get("report_kind") == "MONTHLY"
    form = OperationReportForm(None if monthly else request.POST or None)
    monthly_form = MonthlyReportForm(request.POST if monthly else None) if request.user.role == Role.ADMIN else None
    if request.method == "POST" and monthly and monthly_form.is_valid():
        export = generate_monthly_report(user=request.user, **monthly_form.cleaned_data)
        return redirect("reports:download", public_id=export.public_id)
    if request.method == "POST" and not monthly and form.is_valid():
        export = generate_operation_report(user=request.user, **form.cleaned_data)
        return redirect("reports:download", public_id=export.public_id)
    return render(request, "reports/list.html", {"form": form, "monthly_form": monthly_form, "exports": exports})


@mfa_required
def report_download(request, public_id):
    export = get_object_or_404(ReportExport, public_id=public_id, requested_by=request.user, status="READY")
    from apps.finance.reporting import can_download, allowed_kinds
    if not can_download(request.user):
        raise PermissionDenied("Le téléchargement n’est pas autorisé pour votre rôle actuel.")
    if export.kind.startswith("FINANCE_") and export.kind.removeprefix("FINANCE_") not in allowed_kinds(request.user):
        raise PermissionDenied("Ce rapport n’est plus disponible pour votre rôle.")
    if not export.file:
        raise Http404("Rapport indisponible.")
    try:
        handle = export.file.open("rb")
    except OSError as exc:
        raise Http404("Fichier indisponible.") from exc
    return FileResponse(handle, as_attachment=True, filename=Path(export.file.name).name)
