import uuid

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.permissions import require_finance_access
from apps.accounts.views import mfa_required
from apps.audit.services import record

from .forms import (BatchForm, CountForm, DistributionForm, EntryFormSet, FinancialAccountForm, FinancialPeriodForm,
                    PartyForm, PaymentForm, ReasonForm, RoleForm, RuleForm, ServiceForm)
from .models import (AccountReconciliation, EconomicRule, FinancialAccount, FinancialPeriod, ImportRow, JournalBatch,
                     ServiceCatalog)
from .services import reconcile_account


@mfa_required
def overview(request):
    policy = require_finance_access(request.user, "view_all")
    query = (request.GET.get("q") or "").strip()
    accounts = FinancialAccount.objects.select_related("currency", "responsible_user")
    if query:
        accounts = accounts.filter(Q(code__icontains=query) | Q(name__icontains=query) | Q(service_code__icontains=query))
    page = Paginator(accounts, 25).get_page(request.GET.get("page"))
    for account in page.object_list:
        account.control = reconcile_account(account)
    context = {
        "page": page,
        "query": query,
        "policy": policy,
        "periods": FinancialPeriod.objects.all()[:8],
        "draft_batches": JournalBatch.objects.filter(status="DRAFT").count(),
    }
    template = "finance/_accounts.html" if request.htmx else "finance/overview.html"
    return render(request, template, context)


@mfa_required
def account_create(request):
    require_finance_access(request.user, "administer")
    form = FinancialAccountForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Compte financier créé.")
        return redirect("finance:overview")
    return render(request, "finance/account_form.html", {"form": form})


@mfa_required
def period_create(request):
    require_finance_access(request.user, "administer")
    form = FinancialPeriodForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Période financière créée.")
        return redirect("finance:overview")
    return render(request, "finance/period_form.html", {"form": form})


@mfa_required
def workspace(request, section):
    policy = require_finance_access(request.user, "view_all")
    from apps.stakeholders.models import Stakeholder
    from apps.expenses.models import Expense
    from apps.profits.models import ProfitPeriod, Distribution
    definitions = {
        "parties": ("Parties économiques", Stakeholder.objects.prefetch_related("economic_roles").all()),
        "rules": ("Règles à date d’effet", EconomicRule.objects.select_related("stakeholder", "currency").order_by("-effective_from")),
        "services": ("Catalogue de services", ServiceCatalog.objects.all()),
        "journal": ("Journal financier", JournalBatch.objects.all()),
        "periods": ("Périodes et clôtures", FinancialPeriod.objects.all()),
        "imports": ("Préparation d’import", ImportRow.objects.all()),
        "reconciliation": ("Réconciliation de reprise", AccountReconciliation.objects.select_related("run", "account", "currency").all()),
        "expenses": ("Charges reconnues et paiements", Expense.objects.select_related("currency").prefetch_related("payments").filter(recognition_batch__isnull=False)),
        "profits": ("Résultat et propositions de distribution", ProfitPeriod.objects.filter(finance_period__isnull=False).prefetch_related("allocations")),
        "distributions": ("Distributions approuvées", Distribution.objects.select_related("stakeholder", "allocation__period__currency").filter(approval_batch__isnull=False)),
    }
    if section not in definitions:
        raise Http404
    title, rows = definitions[section]
    if not rows.ordered:
        rows = rows.order_by("pk")
    return render(request, "finance/workspace.html", {"title": title, "section": section, "page": Paginator(rows, 25).get_page(request.GET.get("page")), "policy": policy})


@mfa_required
def create_record(request, kind):
    require_finance_access(request.user, "administer")
    choices = {"party": (PartyForm, "Nouveau dossier de partie prenante"), "role": (RoleForm, "Rôle économique"), "rule": (RuleForm, "Nouvelle règle datée"), "service": (ServiceForm, "Nouveau service")}
    if kind not in choices:
        raise Http404
    form_class, title = choices[kind]
    form = form_class(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            obj = form.save(commit=False)
            if kind == "rule":
                obj.created_by = request.user
            obj.save()
            record(actor=request.user, action="FINANCE_CREATE", instance=obj)
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            return redirect("finance:workspace", section={"party": "parties", "role": "parties", "rule": "rules", "service": "services"}[kind])
    return render(request, "finance/form.html", {"title": title, "form": form})


@mfa_required
def batch_create(request):
    require_finance_access(request.user, "prepare")
    form = BatchForm(request.POST or None, initial={"idempotency_key": uuid.uuid4().hex})
    lines = EntryFormSet(request.POST or None)
    if request.method == "POST" and form.is_valid() and lines.is_valid():
        from .services import prepare_batch
        try:
            entries = [{"account": row.cleaned_data["account"], "currency": row.cleaned_data["account"].currency,
                        "side": row.cleaned_data["side"], "amount": row.cleaned_data["amount"]} for row in lines if row.cleaned_data]
            batch = prepare_batch(actor=request.user, event_type="MANUAL", lines=entries, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            return redirect("finance:batch", pk=batch.pk)
    return render(request, "finance/form.html", {"title": "Préparer un lot", "form": form, "formset": lines})


@mfa_required
def batch_detail(request, pk):
    policy = require_finance_access(request.user, "view_all")
    batch = get_object_or_404(JournalBatch.objects.prefetch_related("entries__account", "entries__currency"), pk=pk)
    return render(request, "finance/batch.html", {"batch": batch, "policy": policy, "reason_form": ReasonForm()})


@require_POST
@mfa_required
def batch_action(request, pk, action):
    require_finance_access(request.user, "approve")
    batch = get_object_or_404(JournalBatch, pk=pk)
    from .services import post_batch, reverse_batch
    try:
        if batch.source_model or batch.event_type in {"EXPENSE_PAYMENT", "PARTNER_PAYMENT"}:
            raise ValidationError("Utilisez l’action du domaine d’origine pour conserver ses statuts et soldes cohérents.")
        if action == "post":
            post_batch(batch_id=pk, actor=request.user)
        elif action == "reverse":
            form = ReasonForm(request.POST)
            if not form.is_valid():
                raise ValidationError("Un motif de contre-passation est requis.")
            reverse_batch(batch_id=pk, actor=request.user, reason=form.cleaned_data["reason"], idempotency_key=f"manual-reversal-{pk}")
        else:
            raise ValidationError("Action inconnue.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("finance:batch", pk=pk)


@mfa_required
def period_detail(request, pk):
    policy = require_finance_access(request.user, "view_all")
    period = get_object_or_404(FinancialPeriod, pk=pk)
    from .closing import period_report, record_count, close_period
    form = CountForm(request.POST or None)
    if request.method == "POST":
        try:
            if request.POST.get("action") == "close":
                close_period(period_id=pk, actor=request.user)
                return redirect("finance:period", pk=pk)
            if form.is_valid():
                data = form.cleaned_data.copy()
                data["account_id"] = data["account_id"].pk
                record_count(period_id=pk, actor=request.user, **data)
                return redirect("finance:period", pk=pk)
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
    return render(request, "finance/period.html", {**period_report(period), "form": form, "policy": policy})


@mfa_required
def report_csv(request, pk):
    require_finance_access(request.user, "view_all")
    import csv
    from .closing import period_report
    report = period_report(get_object_or_404(FinancialPeriod, pk=pk))
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="finance-period-{pk}.csv"'
    writer = csv.writer(response)
    writer.writerow(["Compte", "Devise", "Ouverture", "Clôture théorique", "Réel déclaré", "Écart"])
    for row in report["accounts"]:
        declared = row["count"].declared if row["count"] else None
        code = row["account"].code
        writer.writerow(["'" + code if code.startswith(("=", "+", "-", "@")) else code, row["account"].currency.code,
                         row["opening"], row["closing"], declared, declared - row["closing"] if declared is not None else "MANQUANT"])
    writer.writerow([])
    writer.writerow(["Devise", "Produits", "Charges reconnues", "Résultat", "Trésorerie"])
    for code, values in report["totals"].items():
        writer.writerow([code, values["income"], values["expenses"], values["result"], values["cash"]])
    for issue in report["controls"]:
        writer.writerow(["CONTRÔLE", issue])
    return response


@mfa_required
def payment(request, kind, pk):
    require_finance_access(request.user, "approve")
    if kind not in {"expense", "distribution", "partner"}:
        raise Http404
    form = PaymentForm(request.POST or None, initial={"idempotency_key": uuid.uuid4().hex})
    if kind == "distribution":
        form.fields.pop("amount")
        form.fields.pop("idempotency_key")
        form.fields["account_id"].required = False
    else:
        form.fields.pop("destination")
    if request.method == "POST" and form.is_valid():
        from .events import pay_expense
        from .closing import pay_distribution
        try:
            data = form.cleaned_data.copy()
            data["account_id"] = data["account_id"].pk if data.get("account_id") else None
            if kind == "expense":
                pay_expense(expense_id=pk, actor=request.user, **data)
            elif kind == "distribution":
                pay_distribution(distribution_id=pk, actor=request.user, **data)
            elif kind == "partner":
                from .events import pay_partner
                pay_partner(party_id=pk, actor=request.user, **data)
            else:
                raise ValidationError("Paiement inconnu.")
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            if kind == "partner":
                return redirect("finance:partner", pk=pk)
            return redirect("finance:workspace", section="expenses" if kind == "expense" else "distributions")
    return render(request, "finance/form.html", {"title": f"Paiement #{pk}", "form": form})


@mfa_required
def distribution_proposal(request, pk):
    require_finance_access(request.user, "prepare")
    form = DistributionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        from .closing import propose_distribution
        try:
            propose_distribution(profit_id=pk, actor=request.user, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            return redirect("finance:workspace", section="profits")
    return render(request, "finance/form.html", {"title": "Proposer une distribution", "form": form})


@require_POST
@mfa_required
def distribution_approve(request, pk):
    require_finance_access(request.user, "approve")
    from .closing import approve_distributions
    form = DistributionForm(request.POST)
    try:
        if not form.is_valid():
            raise ValidationError("Le montant de la proposition à approuver est requis.")
        approve_distributions(profit_id=pk, actor=request.user, **form.cleaned_data)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("finance:workspace", section="profits")


@mfa_required
def partner_detail(request, pk):
    policy = require_finance_access(request.user, "view_all")
    from apps.stakeholders.models import Stakeholder
    from apps.operations.models import Operation
    from .services import ledger_balance
    party = get_object_or_404(Stakeholder, pk=pk)
    accounts = FinancialAccount.objects.filter(economic_owner=party).select_related("currency")
    rows = [{"account": account, "balance": ledger_balance(account)} for account in accounts]
    operations = Operation.objects.filter(partner_attribution__stakeholder=party).select_related("currency", "partner_attribution")
    from .models import LedgerEntry
    payments = LedgerEntry.objects.filter(account__economic_owner=party, account__account_type="PARTNER_PAYABLE",
        side="DEBIT", batch__event_type="PARTNER_PAYMENT", batch__status__in=["POSTED", "REVERSED"]).select_related("batch", "currency").order_by("-batch__effective_at")
    return render(request, "finance/partner.html", {"party": party, "accounts": rows, "operations": operations[:100], "payments": payments, "policy": policy})


@mfa_required
def import_review(request, pk):
    require_finance_access(request.user, "administer")
    from .forms import ImportReviewForm
    from .imports import review_import
    row = get_object_or_404(ImportRow, pk=pk)
    form = ImportReviewForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            review_import(row_id=pk, actor=request.user, resolution=form.cleaned_data["resolution"])
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            return redirect("finance:workspace", section="imports")
    return render(request, "finance/import_review.html", {"row": row, "form": form})
