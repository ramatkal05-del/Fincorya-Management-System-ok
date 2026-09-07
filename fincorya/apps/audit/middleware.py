from django.conf import settings

from .context import reset_request_ip, set_request_ip


class AuditRequestContextMiddleware:
    """Expose the current client address to domain-level audit calls."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        address = request.META.get("REMOTE_ADDR")
        if settings.TRUST_PROXY_HEADERS:
            forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
            if forwarded:
                address = forwarded.split(",", 1)[0].strip()
        token = set_request_ip(address)
        try:
            return self.get_response(request)
        finally:
            reset_request_ip(token)
