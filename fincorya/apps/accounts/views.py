import base64
from django.views.decorators.cache import never_cache
import logging
import secrets
from datetime import timedelta
from functools import wraps
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

import qrcode
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout, update_session_auth_hash
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.http import FileResponse, Http404
from django.core.mail import send_mail
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.db import transaction
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.audit.services import record
from .forms import EmailLoginForm, ProfileForm, SecurePasswordChangeForm, TokenForm
from .models import Role, User
from .services import clear_auth_failures, is_throttled, register_auth_failure, regenerate_recovery_codes
from config.business_time import business_day_bounds


logger = logging.getLogger(__name__)
EMAIL_OTP_TTL_SECONDS = 300
MFA_METHODS = {"totp", "email", "recovery"}


def _client_key(request, identity):
    address = request.META.get("REMOTE_ADDR", "unknown")
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            address = forwarded.split(",", 1)[0].strip()
    return f"{address}:{identity}"


def _totp_setup_context(device, form):
    image = qrcode.make(device.config_url)
    stream = BytesIO()
    image.save(stream, format="PNG")
    return {"form": form, "qr_data": base64.b64encode(stream.getvalue()).decode(), "secret": device.key}


def _apply_local_bypass(request):
    if not (settings.DEBUG and settings.LOCAL_AUTH_BYPASS):
        return request.user.is_authenticated
    if request.user.is_authenticated:
        request.session["fincorya_mfa_verified"] = True
        return True
    user = User.objects.filter(email=settings.LOCAL_AUTH_EMAIL, is_active=True).first()
    if user is None:
        return False
    auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    request.user = user
    request.session["fincorya_mfa_verified"] = True
    return True


def mfa_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not _apply_local_bypass(request):
            return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)
        if not settings.MFA_ENABLED:
            return view(request, *args, **kwargs)
        verified = callable(getattr(request.user, "is_verified", None)) and request.user.is_verified()
        session_verified = request.session.get("fincorya_mfa_verified") or request.session.get("fincorya_recovery_verified")
        if not verified and not session_verified:
            if not request.user.totp_enabled:
                return redirect("accounts:totp_setup")
            auth_logout(request)
            messages.error(request, "Veuillez confirmer votre code de sécurité.")
            return redirect("accounts:login")
        return view(request, *args, **kwargs)
    return wrapped


@never_cache
def login_view(request):
    if _apply_local_bypass(request):
        return redirect("dashboard")
    form = EmailLoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        throttle_key = _client_key(request, form.cleaned_data["email"])
        if is_throttled(action="LOGIN", identifier=throttle_key):
            form.add_error(None, "Trop de tentatives. Réessayez dans quelques minutes.")
            return render(request, "accounts/login.html", {"form": form}, status=429)
        user = authenticate(request, username=form.cleaned_data["email"], password=form.cleaned_data["password"])
        if user is None:
            register_auth_failure(action="LOGIN", identifier=throttle_key)
            form.add_error(None, "Adresse e-mail ou mot de passe incorrect.")
        elif not user.is_active:
            form.add_error(None, "Ce compte est désactivé.")
        elif not settings.MFA_ENABLED:
            clear_auth_failures(action="LOGIN", identifier=throttle_key)
            auth_login(request, user)
            return redirect("dashboard")
        elif user.totp_enabled:
            clear_auth_failures(action="LOGIN", identifier=throttle_key)
            request.session.pop("fincorya_recovery_verified", None)
            request.session.pop("fincorya_mfa_verified", None)
            request.session.pop("fincorya_email_otp", None)
            request.session["preauth_user_id"] = user.pk
            return redirect("accounts:verify")
        else:
            clear_auth_failures(action="LOGIN", identifier=throttle_key)
            auth_login(request, user)
            return redirect("accounts:totp_setup")
    return render(request, "accounts/login.html", {"form": form})


def _preauthenticated_user(request):
    user_id = request.session.get("preauth_user_id")
    if not user_id:
        return None
    return User.objects.filter(pk=user_id, is_active=True).first()


def _masked_email(email):
    local, separator, domain = email.partition("@")
    if not separator:
        return "adresse enregistrée"
    visible = local[:2] if len(local) > 2 else local[:1]
    return f"{visible}{'•' * max(3, len(local) - len(visible))}@{domain}"


def _finish_mfa(request, user, method, device=None):
    auth_login(request, user)
    if device is not None:
        otp_login(request, device)
    elif method == "recovery":
        request.session["fincorya_recovery_verified"] = True
    else:
        request.session["fincorya_mfa_verified"] = True
    request.session.pop("preauth_user_id", None)
    request.session.pop("fincorya_email_otp", None)
    record(actor=user, action="MFA_VERIFIED", instance=user, after={"method": method, "success": True})


def send_email_otp(request):
    if request.method != "POST" or not settings.MFA_ENABLED:
        return redirect("accounts:login")
    user = _preauthenticated_user(request)
    if user is None:
        messages.error(request, "Votre session de vérification a expiré. Reconnectez-vous.")
        return redirect("accounts:login")
    throttle_key = _client_key(request, f"email-send:{user.pk}")
    if is_throttled(action="EMAIL_OTP_SEND", identifier=throttle_key):
        messages.error(request, "Trop de codes demandés. Réessayez dans 15 minutes.")
        return redirect(f"{reverse('accounts:verify')}?method=email")
    send_attempt = register_auth_failure(
        action="EMAIL_OTP_SEND", identifier=throttle_key, limit=4, window_minutes=15, block_minutes=15,
    )
    if send_attempt.blocked_until and send_attempt.attempts >= 4:
        messages.error(request, "Trop de codes demandés. Réessayez dans 15 minutes.")
        return redirect(f"{reverse('accounts:verify')}?method=email")
    raw_code = f"{secrets.randbelow(1_000_000):06d}"
    request.session["fincorya_email_otp"] = {
        "user_id": user.pk,
        "code_hash": make_password(raw_code),
        "expires_at": (timezone.now() + timedelta(seconds=EMAIL_OTP_TTL_SECONDS)).timestamp(),
    }
    try:
        send_mail(
            "Votre code de sécurité FINCORYA",
            f"Votre code de vérification FINCORYA est : {raw_code}\n\nIl expire dans 5 minutes. Ne le communiquez à personne.",
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            fail_silently=False,
        )
    except Exception:
        request.session.pop("fincorya_email_otp", None)
        logger.exception("Échec d'envoi du code MFA par e-mail pour l'utilisateur %s", user.pk)
        record(actor=user, action="MFA_EMAIL_DELIVERY_FAILED", instance=user, after={"method": "email"})
        messages.error(request, "Le code n’a pas pu être envoyé. Utilisez l’application ou réessayez plus tard.")
    else:
        record(actor=user, action="MFA_EMAIL_SENT", instance=user, after={"method": "email"})
        messages.success(request, f"Un code a été envoyé à {_masked_email(user.email)}. Il expire dans 5 minutes.")
    return redirect(f"{reverse('accounts:verify')}?method=email")


@never_cache
def verify_view(request):
    if not settings.MFA_ENABLED:
        return redirect("dashboard" if request.user.is_authenticated else "accounts:login")
    user = _preauthenticated_user(request)
    if not user:
        request.session.pop("preauth_user_id", None)
        messages.error(request, "Votre session de vérification a expiré. Reconnectez-vous.")
        return redirect("accounts:login")
    method = request.POST.get("method") or request.GET.get("method", "totp")
    if method not in MFA_METHODS:
        method = "totp"
    form = TokenForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        throttle_key = _client_key(request, f"{method}:{user.pk}")
        if is_throttled(action="TOTP", identifier=throttle_key):
            form.add_error("token", "Trop de tentatives. Réessayez dans quelques minutes.")
            return render(request, "accounts/verify.html", {
                "form": form, "method": method, "masked_email": _masked_email(user.email),
            }, status=429)
        token = form.cleaned_data["token"].replace(" ", "")
        verified = False
        device = None
        recovery = None
        if method == "totp":
            device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
            verified = bool(device and device.verify_token(token))
        elif method == "email":
            email_otp = request.session.get("fincorya_email_otp") or {}
            verified = bool(
                email_otp.get("user_id") == user.pk
                and email_otp.get("expires_at", 0) > timezone.now().timestamp()
                and check_password(token, email_otp.get("code_hash", ""))
            )
            if email_otp and email_otp.get("expires_at", 0) <= timezone.now().timestamp():
                request.session.pop("fincorya_email_otp", None)
                form.add_error("token", "Ce code e-mail a expiré. Demandez-en un nouveau.")
        else:
            recovery = next((code for code in user.recovery_codes.filter(used_at__isnull=True) if code.matches(token.upper())), None)
            # Only the request that atomically consumes the unused code may log in.
            verified = recovery is not None and user.recovery_codes.filter(
                pk=recovery.pk, used_at__isnull=True,
            ).update(used_at=timezone.now()) == 1
        if verified:
            if recovery is not None:
                messages.warning(request, "Code de récupération utilisé. Il ne sera plus valide.")
            _finish_mfa(request, user, method, device=device)
            clear_auth_failures(action="TOTP", identifier=throttle_key)
            if method == "email":
                clear_auth_failures(action="EMAIL_OTP_SEND", identifier=_client_key(request, f"email-send:{user.pk}"))
            return redirect("dashboard")
        register_auth_failure(action="TOTP", identifier=throttle_key, limit=6)
        record(actor=user, action="MFA_FAILED", instance=user, after={"method": method, "success": False})
        if not form.errors:
            form.add_error("token", "Code invalide ou expiré.")
    return render(request, "accounts/verify.html", {
        "form": form, "method": method, "masked_email": _masked_email(user.email),
        "email_code_active": bool(request.session.get("fincorya_email_otp")),
    })


@never_cache
@login_required
def totp_setup(request):
    if not settings.MFA_ENABLED:
        return redirect("dashboard")
    if request.user.totp_enabled:
        return redirect("dashboard")
    device, _ = TOTPDevice.objects.get_or_create(user=request.user, name="FINCORYA", confirmed=False)
    form = TokenForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        throttle_key = _client_key(request, f"setup:{request.user.pk}")
        if is_throttled(action="TOTP_SETUP", identifier=throttle_key):
            form.add_error("token", "Trop de tentatives. Réessayez dans quelques minutes.")
            return render(request, "accounts/totp_setup.html", _totp_setup_context(device, form), status=429)
        if device.verify_token(form.cleaned_data["token"]):
            device.confirmed = True
            device.save(update_fields=["confirmed"])
            request.user.totp_enabled = True
            request.user.totp_confirmed_at = timezone.now()
            request.user.save(update_fields=["totp_enabled", "totp_confirmed_at"])
            otp_login(request, device)
            codes = regenerate_recovery_codes(request.user)
            request.session["new_recovery_codes"] = codes
            clear_auth_failures(action="TOTP_SETUP", identifier=throttle_key)
            return redirect("accounts:recovery_codes")
        register_auth_failure(action="TOTP_SETUP", identifier=throttle_key, limit=6)
        form.add_error("token", "Le code ne correspond pas. Vérifiez l'heure de votre téléphone.")
    return render(request, "accounts/totp_setup.html", _totp_setup_context(device, form))


@mfa_required
def recovery_codes(request):
    codes = request.session.pop("new_recovery_codes", None)
    if not codes:
        return redirect("dashboard")
    return render(request, "accounts/recovery_codes.html", {"codes": codes})



@never_cache
@login_required
def logout_view(request):
    if request.method == "POST":
        auth_logout(request)
        return redirect("accounts:login")
    return redirect("dashboard")


@mfa_required
def profile_edit(request):
    user = request.user
    before = {
        "first_name": user.first_name, "last_name": user.last_name, "email": user.email,
        "phone": user.phone, "city": user.city, "language": user.language,
    }
    old_photo_name = user.photo.name if user.photo else ""
    old_photo_storage = user.photo.storage if user.photo else None
    form = ProfileForm(request.POST or None, request.FILES or None, instance=user)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            updated_user = form.save(commit=False)
            if user.username == before["email"]:
                updated_user.username = updated_user.email
            updated_user.save()
            updated_user.owned_stakeholders.update(
                name=updated_user.get_full_name() or updated_user.email,
                email=updated_user.email,
            )
            after = {
                "first_name": updated_user.first_name, "last_name": updated_user.last_name,
                "email": updated_user.email, "phone": updated_user.phone,
                "city": updated_user.city, "language": updated_user.language,
            }
            record(actor=updated_user, action="PROFILE_UPDATE", instance=updated_user, before=before, after=after)
            new_photo_name = updated_user.photo.name if updated_user.photo else ""
            if old_photo_name and old_photo_name != new_photo_name and old_photo_storage:
                transaction.on_commit(lambda: old_photo_storage.delete(old_photo_name), robust=True)
        messages.success(request, "Votre profil a été mis à jour.")
        return redirect("accounts:profile")
    return render(request, "accounts/profile.html", {"form": form})


@mfa_required
def password_change(request):
    form = SecurePasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        record(actor=user, action="PASSWORD_CHANGE", instance=user, after={"password_changed": True})
        messages.success(request, "Votre mot de passe a été modifié. Votre session reste active.")
        return redirect("accounts:profile")
    return render(request, "accounts/password_change.html", {"form": form})


@mfa_required
def profile_photo(request, user_id):
    if request.user.pk != user_id and request.user.role != Role.ADMIN:
        raise Http404("Photo indisponible.")
    user = User.objects.filter(pk=user_id).first()
    if user is None or not user.photo:
        raise Http404("Photo indisponible.")
    try:
        handle = user.photo.open("rb")
    except OSError as exc:
        raise Http404("Photo indisponible.") from exc
    response = FileResponse(handle, filename=Path(user.photo.name).name)
    response.headers["Cache-Control"] = "private, max-age=300, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@mfa_required
def dashboard(request):
    from apps.cash.models import CashAccount, HandoverStatus
    from apps.expenses.models import Expense, ExpenseStatus
    from apps.operations.models import Operation, OperationStatus

    accounts = CashAccount.objects.select_related("currency", "agent")
    if request.user.role == Role.AGENT:
        accounts = accounts.filter(agent=request.user)
    elif request.user.role != Role.ADMIN:
        accounts = accounts.none()
    operations = Operation.objects.select_related("currency", "agent")
    if request.user.role == Role.AGENT:
        operations = operations.filter(agent=request.user)
    elif request.user.role != Role.ADMIN:
        operations = operations.none()
    recent_operations = operations[:7]
    pending_count = operations.filter(status=OperationStatus.PENDING).count()
    completed_count = operations.filter(status=OperationStatus.COMPLETED).count()
    total_operations = operations.count()
    success_rate = round(completed_count * 100 / total_operations) if total_operations else 0
    fee_totals = list(
        operations.filter(status=OperationStatus.COMPLETED)
        .values("currency__code").annotate(total=Sum("fee")).order_by("currency__code")
    )
    today = timezone.localdate()
    first_day = today - timedelta(days=6)
    range_start, range_end = business_day_bounds(first_day, today)
    activity_counts = {
        row["day"]: row["count"]
        for row in operations.filter(created_at__gte=range_start, created_at__lt=range_end)
        .annotate(day=TruncDate("created_at", tzinfo=ZoneInfo(settings.BUSINESS_TIME_ZONE)))
        .values("day").annotate(count=Count("id"))
    }
    activity_raw = [
        {"date": first_day + timedelta(days=offset), "count": activity_counts.get(first_day + timedelta(days=offset), 0)}
        for offset in range(7)
    ]
    activity_max = max((point["count"] for point in activity_raw), default=0) or 1
    activity_points = [
        {**point, "height": max(8, round(point["count"] * 100 / activity_max))}
        for point in activity_raw
    ]
    type_rows = list(operations.values("type").annotate(count=Count("id")).order_by("-count"))
    type_labels = dict(Operation._meta.get_field("type").choices)
    operation_mix = [
        {"type": row["type"], "label": type_labels.get(row["type"], row["type"]), "count": row["count"], "percent": round(row["count"] * 100 / total_operations) if total_operations else 0}
        for row in type_rows
    ]
    pending_handovers = accounts.filter(handovers__status=HandoverStatus.PENDING).distinct().count()
    pending_expenses = Expense.objects.filter(status=ExpenseStatus.PENDING).count() if request.user.role == Role.ADMIN else 0
    notifications = request.user.notifications.filter(read_at__isnull=True)[:4]
    return render(request, "dashboard.html", {
        "cash_accounts": accounts,
        "recent_operations": recent_operations,
        "pending_count": pending_count,
        "completed_count": completed_count,
        "total_operations": total_operations,
        "success_rate": success_rate,
        "fee_totals": fee_totals,
        "activity_points": activity_points,
        "operation_mix": operation_mix,
        "pending_handovers": pending_handovers,
        "pending_expenses": pending_expenses,
        "unread_notifications": notifications,
    })
