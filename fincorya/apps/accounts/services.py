import secrets
import hashlib
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from .models import AuthThrottle, RecoveryCode


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
