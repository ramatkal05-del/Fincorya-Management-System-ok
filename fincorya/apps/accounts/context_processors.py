from django.conf import settings


def security_settings(request):
    return {"mfa_enabled": settings.MFA_ENABLED}
