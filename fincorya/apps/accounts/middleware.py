from django.conf import settings
from django.contrib.auth import login


class LocalAuthenticationBypassMiddleware:
    """Automatically opens a local debug session as the configured demo user."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.DEBUG and settings.LOCAL_AUTH_BYPASS and not request.user.is_authenticated:
            from .models import User

            user = User.objects.filter(email=settings.LOCAL_AUTH_EMAIL, is_active=True).first()
            if user is not None:
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                request.user = user
                request.session["fincorya_mfa_verified"] = True
        return self.get_response(request)
