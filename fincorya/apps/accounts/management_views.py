"""Administrator-only account creation, separate from economic records."""
from django import forms
from django.contrib import messages
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.audit.services import record
from .models import Role, User
from .services import issue_activation_token
from .views import mfa_required


class ManagedAccountForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("first_name", "last_name", "email", "phone", "city",
                  "agent_started_on", "agent_ended_on", "monthly_salary_usd")
        labels = {"monthly_salary_usd": "Salaire mensuel (USD)"}
        widgets = {"agent_started_on": forms.DateInput(attrs={"type": "date"}),
                   "agent_ended_on": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, role, **kwargs):
        self.account_role = role
        super().__init__(*args, **kwargs)
        if role != Role.AGENT:
            for field in ("agent_started_on", "agent_ended_on", "monthly_salary_usd"):
                self.fields.pop(field)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Cette adresse e-mail est déjà utilisée.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = self.account_role
        user.is_staff = self.account_role == Role.ADMIN
        user.is_superuser = False
        if commit:
            user.save()
        return user


def require_admin(user):
    if not user.is_active or user.role != Role.ADMIN:
        raise PermissionDenied("Seul l’administrateur peut gérer les comptes utilisateurs.")


@mfa_required
def account_list(request):
    require_admin(request.user)
    selected = request.GET.get("role", "")
    users = User.objects.order_by("role", "email")
    if selected in Role.values:
        users = users.filter(role=selected)
    return render(request, "accounts/management_list.html", {
        "users": users, "account_roles": Role.choices, "selected_role": selected})


@mfa_required
def account_create(request, role):
    require_admin(request.user)
    if role not in Role.values:
        from django.http import Http404
        raise Http404
    form = ManagedAccountForm(request.POST or None, role=role)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save()
            record(actor=request.user, action="USER_CREATE", instance=user,
                   after={"email": user.email, "role": user.role})
            _, raw_token = issue_activation_token(user=user, actor=request.user)
            transaction.on_commit(lambda: _send_welcome_email(user, raw_token))
        messages.success(request, f"Compte {user.get_role_display().lower()} créé : {user.email}. Un e-mail de bienvenue a été mis en file d'envoi.")
        return redirect("accounts:manage")
    return render(request, "accounts/management_form.html", {
        "form": form, "role_label": Role(role).label, "role": role})


def _send_welcome_email(user, raw_token):
    from apps.notifications.senders import send_welcome_email
    send_welcome_email(user=user, raw_token=raw_token)


@require_POST
@mfa_required
def account_resend_invitation(request, pk):
    require_admin(request.user)
    user = get_object_or_404(User, pk=pk, is_active=True)
    with transaction.atomic():
        _, raw_token = issue_activation_token(user=user, actor=request.user)
        record(actor=request.user, action="USER_INVITATION_RESEND", instance=user)
        transaction.on_commit(lambda: _send_welcome_email(user, raw_token))
    messages.success(request, f"Invitation renvoyée à {user.email}. Le lien précédent est désormais invalide.")
    return redirect("accounts:manage")


@require_POST
@mfa_required
def account_status(request, pk):
    require_admin(request.user)
    with transaction.atomic():
        user = get_object_or_404(User.objects.select_for_update(), pk=pk)
        if user.pk == request.user.pk or user.is_superuser:
            raise PermissionDenied("Ce compte administrateur ne peut pas être désactivé ici.")
        user.is_active = not user.is_active
        user.save(update_fields=["is_active"])
        record(actor=request.user, action="USER_STATUS", instance=user, after={"is_active": user.is_active})
    return redirect("accounts:manage")
