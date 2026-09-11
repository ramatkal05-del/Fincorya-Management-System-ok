from pathlib import Path

from django.core.exceptions import PermissionDenied
from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.urls import reverse

from apps.accounts.models import Role
from apps.accounts.views import mfa_required
from .forms import FinanceReportForm, MonthlyReportForm
from .models import ReportExport
from .services import generate_finance_report, generate_monthly_report


@mfa_required
def report_center(request):
    """Single entry point for every role: standard reports plus, for admins, the
    monthly consolidated export. Kinds, periods and scope are decided server-side."""
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
                if request.htmx:
                    return HttpResponse(headers={"HX-Redirect": reverse("reports:download", args=[export.public_id])})
                return redirect("reports:download", public_id=export.public_id)
        except (PermissionDenied, ValueError) as exc:
            error = str(exc)
        except Exception as exc:  # ValidationError from period parsing
            error = "; ".join(getattr(exc, "messages", [str(exc)]))
    exports = ReportExport.objects.filter(requested_by=request.user, kind__in=[
        *(f"FINANCE_{kind}" for kind in allowed_kinds(request.user)), "MONTHLY_FINANCIAL",
    ]).order_by("-created_at")[:30]
    monthly_form = MonthlyReportForm(auto_id="monthly_%s") if request.user.role == Role.ADMIN else None
    template = "reports/_center_result.html" if request.htmx else "reports/center.html"
    return render(request, template, {
        "form": form, "report": report, "error": error, "exports": exports,
        "can_download": can_download(request.user), "monthly_form": monthly_form,
        "business_timezone": settings.BUSINESS_TIME_ZONE,
    })


@require_POST
@mfa_required
def monthly_report_create(request):
    """Admin-only consolidated monthly export (salaries, investments, stakeholder
    fiches) — a distinct deliverable from the standard finance reports above,
    kept on the same screen instead of a second, overlapping report menu."""
    if request.user.role != Role.ADMIN:
        raise PermissionDenied("Le rapport mensuel consolidé est réservé à l’administrateur.")
    form = MonthlyReportForm(request.POST, auto_id="monthly_%s")
    if form.is_valid():
        export = generate_monthly_report(user=request.user, **form.cleaned_data)
        return redirect("reports:download", public_id=export.public_id)
    from apps.finance.reporting import allowed_kinds, can_download
    exports = ReportExport.objects.filter(requested_by=request.user, kind__in=[
        *(f"FINANCE_{kind}" for kind in allowed_kinds(request.user)), "MONTHLY_FINANCIAL",
    ]).order_by("-created_at")[:30]
    return render(request, "reports/center.html", {
        "form": FinanceReportForm(user=request.user), "report": None, "error": None,
        "exports": exports, "can_download": can_download(request.user), "monthly_form": form,
        "business_timezone": settings.BUSINESS_TIME_ZONE,
    })


@mfa_required
def report_download(request, public_id):
    export = get_object_or_404(ReportExport, public_id=public_id, requested_by=request.user, status="READY")
    from apps.finance.reporting import can_download, allowed_kinds
    if not can_download(request.user):
        raise PermissionDenied("Le téléchargement n’est pas autorisé pour votre rôle actuel.")
    if export.kind == "MONTHLY_FINANCIAL" and request.user.role != Role.ADMIN:
        raise PermissionDenied("Le rapport mensuel consolidé est réservé à l’administrateur.")
    if export.kind.startswith("FINANCE_") and export.kind.removeprefix("FINANCE_") not in allowed_kinds(request.user):
        raise PermissionDenied("Ce rapport n’est plus disponible pour votre rôle.")
    if not export.file:
        raise Http404("Rapport indisponible.")
    try:
        handle = export.file.open("rb")
    except OSError as exc:
        raise Http404("Fichier indisponible.") from exc
    return FileResponse(handle, as_attachment=True, filename=Path(export.file.name).name)
