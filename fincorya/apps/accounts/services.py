import secrets
import hashlib
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from .models import AccountActivationToken, AuthThrottle, RecoveryCode

ACTIVATION_TOKEN_TTL_HOURS = 72


@transaction.atomic
def issue_activation_token(*, user, actor):
    """Invalidate any still-usable link for this user and issue a fresh one.

    Used both for the initial welcome e-mail and for an administrator's
    "resend invitation" action — either way, only the newest link works.
    """
    AccountActivationToken.objects.filter(user=user, used_at__isnull=True, invalidated_at__isnull=True).update(invalidated_at=timezone.now())
    raw_token = secrets.token_urlsafe(32)
    token = AccountActivationToken(user=user, created_by=actor, expires_at=timezone.now() + timedelta(hours=ACTIVATION_TOKEN_TTL_HOURS))
    token.set_token(raw_token)
    token.save()
    return token, raw_token


def get_valid_activation_token(*, user_id, raw_token):
    for token in AccountActivationToken.objects.filter(user_id=user_id, used_at__isnull=True, invalidated_at__isnull=True).order_by("-created_at"):
        if token.is_valid() and token.matches(raw_token):
            return token
    return None


@transaction.atomic
def consume_activation_token(*, token, new_password):
    user = token.user
    user.set_password(new_password)
    user.save(update_fields=["password"])
    token.used_at = timezone.now()
    token.save(update_fields=["used_at"])
    return user


@transaction.atomic
def regenerate_recovery_codes(user, count=8):
    user.recovery_codes.filter(used_at__isnull=True).delete()
    raw_codes = []
    for _ in range(count):
        raw = f"{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}"
        code = RecoveryCode(user=user)
        code.set_code(raw)
        code.save()
        raw_codes.append(raw)
    return raw_codes


def _throttle_hash(identifier):
    return hashlib.sha256(identifier.strip().lower().encode("utf-8")).hexdigest()


def is_throttled(*, action, identifier):
    row = AuthThrottle.objects.filter(action=action, key_hash=_throttle_hash(identifier)).first()
    return bool(row and row.blocked_until and row.blocked_until > timezone.now())


@transaction.atomic
def register_auth_failure(*, action, identifier, limit=5, window_minutes=15, block_minutes=15):
    now = timezone.now()
    key_hash = _throttle_hash(identifier)
    row, _ = AuthThrottle.objects.select_for_update().get_or_create(
        action=action, key_hash=key_hash, defaults={"window_started": now}
    )
    if row.window_started <= now - timedelta(minutes=window_minutes):
        row.window_started, row.attempts = now, 0
    row.attempts += 1
    if row.attempts >= limit:
        row.blocked_until = now + timedelta(minutes=block_minutes)
    row.save(update_fields=["window_started", "attempts", "blocked_until"])
    return row


def clear_auth_failures(*, action, identifier):
    AuthThrottle.objects.filter(action=action, key_hash=_throttle_hash(identifier)).delete()
