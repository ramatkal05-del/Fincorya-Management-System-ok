import uuid

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.permissions import require_finance_access
from apps.accounts.views import mfa_required
from apps.audit.services import record

from .forms import (AgentCashForm, BatchForm, ContributionForm, ConversionForm, CountForm, DecisionForm, DistributionForm,
                    EntryFormSet, ExecutionForm, FinancialAccountForm, FinancialPeriodForm, GuaranteeForm, PartyForm,
                    PartyOnboardingForm, PaymentForm, PolicyForm, ReasonForm, RemunerationTermsForm, RoleForm, RuleForm,
                    ServiceForm, StakeholderRequestForm, TransferForm)
from .models import (AccountReconciliation, CurrencyConversion, DistributionPolicy, DistributionPolicyShare, EconomicRule,
                     FinancialAccount, FinancialPeriod, FundContribution, ImportRow, InternalTransfer, JournalBatch,
                     PartnerGuarantee, RemunerationTerms, ServiceCatalog, StakeholderRequest)
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
    from .funds import require_transfer_access
    policy = require_transfer_access(request.user) if section == "transfers" else require_finance_access(request.user, "view_all")
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
        "contributions": ("Apports reçus", FundContribution.objects.select_related("stakeholder", "currency", "receiving_account", "batch")),
        "transfers": ("Transferts internes et fonds en transit", InternalTransfer.objects.select_related("source", "destination", "currency", "initiated_by")),
        "requests": ("Demandes des parties prenantes", StakeholderRequest.objects.select_related("stakeholder", "currency", "distribution")),
        "guarantees": ("Garanties et plafonds partenaires", PartnerGuarantee.objects.select_related("stakeholder", "currency")),
        "policies": ("Politiques de distribution datées", DistributionPolicy.objects.prefetch_related("shares__stakeholder").all()),
        "conversions": ("Conversions et écarts de change", CurrencyConversion.objects.select_related("source", "destination", "batch")),
        "remunerations": ("Conditions contractuelles de rémunération", RemunerationTerms.objects.select_related("stakeholder", "updated_by")),
    }
    if section not in definitions:
        raise Http404
    title, rows = definitions[section]
    if policy.own_cash_only:
        rows = rows.filter(Q(source__responsible_user=request.user) | Q(destination__responsible_user=request.user))
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


def _run(form, action):
    """Run a service call from a valid form; surface business errors on the form."""
    try:
        return action()
    except ValidationError as exc:
        form.add_error(None, "; ".join(exc.messages))
        return None


@mfa_required
def contribution_create(request):
    require_finance_access(request.user, "approve")
    from .funds import record_contribution
    form = ContributionForm(request.POST or None, initial={"client_key": uuid.uuid4().hex})
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data.copy()
        data["stakeholder_id"], data["account_id"] = data["stakeholder_id"].pk, data["account_id"].pk
        if _run(form, lambda: record_contribution(actor=request.user, **data)):
            messages.success(request, "Apport enregistré une seule fois et affecté à son compte de réception.")
            return redirect("finance:workspace", section="contributions")
    return render(request, "finance/form.html", {"title": "Enregistrer un apport reçu", "form": form, "confirm": "Confirmez l’encaissement : une écriture définitive sera publiée."})


@mfa_required
def transfer_create(request):
    from .funds import initiate_transfer, require_transfer_access
    require_transfer_access(request.user)
    form = TransferForm(request.POST or None, user=request.user, initial={"client_key": uuid.uuid4().hex})
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data.copy()
        data["source_id"], data["destination_id"] = data["source_id"].pk, data["destination_id"].pk
        if _run(form, lambda: initiate_transfer(actor=request.user, **data)):
            messages.success(request, "Transfert initié : les fonds sont en transit jusqu’à confirmation de réception.")
            return redirect("finance:workspace", section="transfers")
    return render(request, "finance/form.html", {"title": "Transfert interne", "form": form, "confirm": "Les fonds quittent la source immédiatement et restent en transit."})


@require_POST
@mfa_required
def transfer_action(request, pk, action):
    if action in {"receive", "return"}:
        from .funds import require_transfer_access
        require_transfer_access(request.user)
    else:
        require_finance_access(request.user, "approve")
    from .funds import cancel_transfer, confirm_transfer, receive_transfer, confirm_transfer_return
    try:
        if action == "return":
            confirm_transfer_return(actor=request.user, transfer_id=pk, note=request.POST.get("reason", ""))
            messages.success(request, "Retour confirmé ; l’administrateur peut annuler et recréditer la source.")
        elif action == "receive":
            receive_transfer(actor=request.user, transfer_id=pk)
            messages.success(request, "Réception confirmée ; les fonds restent en transit jusqu’à validation finale.")
        elif action == "confirm":
            confirm_transfer(actor=request.user, transfer_id=pk)
            messages.success(request, "Transfert validé ; les fonds sont sortis du transit.")
        elif action == "cancel":
            form = ReasonForm(request.POST)
            if not form.is_valid():
                raise ValidationError("Un motif d’annulation est requis.")
            cancel_transfer(actor=request.user, transfer_id=pk, reason=form.cleaned_data["reason"])
            messages.success(request, "Transfert annulé ; les fonds sont revenus à la source.")
        else:
            raise Http404
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("finance:workspace", section="transfers")


@mfa_required
def guarantee_form(request):
    require_finance_access(request.user, "administer")
    from .funds import set_partner_guarantee
    form = GuaranteeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data.copy()
        data["stakeholder_id"] = data["stakeholder_id"].pk
        if _run(form, lambda: set_partner_guarantee(actor=request.user, **data)):
            return redirect("finance:workspace", section="guarantees")
    return render(request, "finance/form.html", {"title": "Garantie et plafond par opération", "form": form})


@mfa_required
def policy_create(request):
    require_finance_access(request.user, "administer")
    form = PolicyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        def save():
            from .policies import create_distribution_policy
            data = form.cleaned_data
            return create_distribution_policy(actor=request.user, mode=data["mode"], effective_from=data["effective_from"],
                                              shares=data.get("shares"), notes=data["notes"])
        if _run(form, save):
            return redirect("finance:workspace", section="policies")
    return render(request, "finance/form.html", {"title": "Nouvelle politique de distribution datée", "form": form,
        "confirm": "La politique est figée dès sa création ; les périodes déjà clôturées gardent leur propre politique."})


@mfa_required
def party_onboarding(request):
    require_finance_access(request.user, "administer")
    from .funds import onboard_party
    form = PartyOnboardingForm(request.POST or None, request.FILES or None, initial={"client_key": uuid.uuid4().hex})
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        if _run(form, lambda: onboard_party(actor=request.user, party_type=data["party_type"], identity=form.identity(),
                                            currency=data.get("currency"), per_operation_ceiling=data.get("per_operation_ceiling"),
                                            deposit=form.deposit())):
            messages.success(request, "Partie créée manuellement ; aucun montant n’a été prérempli.")
            return redirect("finance:workspace", section="parties")
    return render(request, "finance/form.html", {"title": "Créer une partie (partenaire, investisseur, actionnaire)",
        "form": form, "confirm": "Tous les montants saisis doivent correspondre à des fonds réellement reçus ou repris."})


@mfa_required
def remuneration_terms(request):
    require_finance_access(request.user, "administer")
    form = RemunerationTermsForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        def save():
            terms = form.save(commit=False)
            terms.updated_by = request.user
            terms.save()
            record(actor=request.user, action="REMUNERATION_TERMS", instance=terms)
            return terms
        if _run(form, save):
            return redirect("finance:workspace", section="remunerations")
    return render(request, "finance/form.html", {"title": "Conditions contractuelles de rémunération", "form": form,
        "confirm": "Sans règles explicites de mois incomplet ou déficitaire, la clôture du mois concerné sera bloquée."})


@mfa_required
def agent_cash_open(request):
    policy = require_finance_access(request.user, "prepare")
    from .funds import open_agent_cash
    if not (policy.administer or policy.prepare):
        raise PermissionDenied
    form = AgentCashForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if _run(form, lambda: open_agent_cash(actor=request.user, agent=form.cleaned_data["agent"], currency=form.cleaned_data["currency"])):
            messages.success(request, "Caisse agent créée sans solde ; l’allocation de fonds reste un mouvement séparé.")
            return redirect("finance:overview")
    return render(request, "finance/form.html", {"title": "Ouvrir une caisse agent", "form": form})


@mfa_required
def conversion_create(request):
    require_finance_access(request.user, "approve")
    from .funds import record_conversion
    form = ConversionForm(request.POST or None, initial={"client_key": uuid.uuid4().hex})
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data.copy()
        data["source_id"], data["destination_id"] = data["source_id"].pk, data["destination_id"].pk
        if _run(form, lambda: record_conversion(actor=request.user, **data)):
            messages.success(request, "Conversion enregistrée ; l’écart réalisé est isolé hors du distribuable.")
            return redirect("finance:workspace", section="conversions")
    return render(request, "finance/form.html", {"title": "Conversion de devises", "form": form,
        "confirm": "L’écart de change réalisé est comptabilisé séparément et exclu du montant distribuable."})


@mfa_required
def request_decide(request, pk):
    require_finance_access(request.user, "approve")
    from .funds import decide_request, execute_request
    row = get_object_or_404(StakeholderRequest.objects.select_related("stakeholder", "currency"), pk=pk)
    decision, execution = DecisionForm(request.POST or None, prefix="d"), ExecutionForm(request.POST or None, prefix="e")
    if request.method == "POST":
        if request.POST.get("action") == "decide" and decision.is_valid():
            if _run(decision, lambda: decide_request(actor=request.user, request_id=pk, **decision.cleaned_data)):
                return redirect("finance:request_decide", pk=pk)
        elif request.POST.get("action") == "execute" and execution.is_valid():
            account = execution.cleaned_data.get("account_id")
            if _run(execution, lambda: execute_request(actor=request.user, request_id=pk, account_id=account.pk if account else None)):
                messages.success(request, "Demande exécutée ; l’écriture est publiée une seule fois.")
                return redirect("finance:workspace", section="requests")
    return render(request, "finance/request.html", {"row": row, "decision": decision, "execution": execution, "policy": require_finance_access(request.user, "view_all")})


@mfa_required
def party_space(request):
    """Shareholder, investor or partner: own situation, requests, and (shareholder) global read-only view."""
    from apps.accounts.permissions import finance_policy, linked_party
    from .funds import submit_request
    from .reporting import PARTY_KIND, build_report, can_download
    policy = finance_policy(request.user)
    party = linked_party(request.user)
    if party is None:
        raise PermissionDenied("Aucune partie prenante n’est liée à votre compte ; contactez l’administrateur.")
    form = StakeholderRequestForm(request.POST or None, party=party)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data.copy()
        if data.get("distribution_id") is not None:
            data["distribution_id"] = data["distribution_id"].pk
        else:
            data.pop("distribution_id", None)
        if _run(form, lambda: submit_request(actor=request.user, **data)):
            messages.success(request, "Demande soumise ; elle n’agit qu’après validation et exécution par l’administration.")
            return redirect("finance:party_space")
    situation = build_report(user=request.user, kind=PARTY_KIND[party.type], preset="MONTH", anchor=timezone.localdate())
    global_view = build_report(user=request.user, kind="ACTIVITY", preset="MONTH", anchor=timezone.localdate()) if policy.global_read_only else None
    requests_rows = StakeholderRequest.objects.filter(stakeholder=party).select_related("currency")[:50]
    context = {"party": party, "form": form, "situation": situation, "global_view": global_view, "requests": requests_rows,
               "refreshed_at": timezone.now(), "can_download": can_download(request.user)}
    if party.type == "PARTNER":
        from .forms import CommissionChoiceForm
        context["commission_form"] = CommissionChoiceForm()
        context["commission_choices"] = party.commission_choices.select_related("chosen_by")[:20]
    return render(request, "finance/_party_situation.html" if request.htmx else "finance/party_space.html", context)


@mfa_required
def commission_choice(request):
    from apps.accounts.permissions import linked_party
    from .commissions import choose_commission_destination
    from .forms import CommissionChoiceForm
    party = linked_party(request.user)
    if party is None or party.type != "PARTNER":
        raise PermissionDenied("Ce choix appartient au partenaire connecté.")
    form = CommissionChoiceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if _run(form, lambda: choose_commission_destination(actor=request.user, **form.cleaned_data)):
            messages.success(request, "Choix enregistré pour les nouvelles commissions à partir de sa date d’effet.")
            return redirect("finance:party_space")
    return render(request, "finance/form.html", {"title": "Destination de mes commissions", "form": form})


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
