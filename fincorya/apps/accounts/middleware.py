from django.conf import settings
from django.contrib.auth import login
from django.utils.deprecation import MiddlewareMixin


class AdminMFAMiddleware(MiddlewareMixin):
    """Apply the application's MFA gate to every resolved admin view."""

    def process_view(self, request, view_func, view_args, view_kwargs):
        if settings.MFA_ENABLED and request.resolver_match.app_name == "admin":
            from .views import mfa_required

            return mfa_required(lambda request: None)(request)


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
