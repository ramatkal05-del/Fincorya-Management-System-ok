from .models import AuditEvent
from .context import get_request_ip


def record(*, actor, action: str, instance, before: dict | None = None, after: dict | None = None, ip_address: str | None = None):
    """One call-site used by every service below — never write audit rows ad hoc from views."""
    return AuditEvent.objects.create(
        actor=actor,
        action=action,
        model_name=instance.__class__.__name__,
        object_id=str(instance.pk),
        before=before or {},
        after=after or {},
        ip_address=ip_address or get_request_ip(),
    )
