from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.db import connection
from django.urls import include, path, re_path
from django.templatetags.static import static
from django.views.generic import RedirectView
from django.views.static import serve
from apps.accounts.views import dashboard

def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "unavailable", "service": "fincorya"}, status=503)
    return JsonResponse({"status": "ok", "service": "fincorya"})

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("favicon.ico", RedirectView.as_view(url=static("img/favicon.svg"), permanent=True)),
    path("auth/", include("apps.accounts.urls")),
    path("operations/", include("apps.operations.urls")),
    path("finance/", include("apps.finance.urls")),
    path("caisses/", include("apps.cash.urls")),
    path("charges/", include("apps.expenses.urls")),
    path("rapports/", include("apps.reports.urls")),
    path("", dashboard, name="dashboard"),
    # apps.accounts / apps.cash / apps.operations / ... URLconfs plug in here (M1-M5).
]

# Serve /assets/ from static_src in development so browser extensions
# that request fonts at /assets/fonts/Inter-*.woff2 get a valid response.
if settings.DEBUG:
    urlpatterns += [
        re_path(
            r"^assets/(?P<path>.*)$",
            serve,
            {"document_root": settings.STATICFILES_DIRS[0] / "assets"},
        ),
    ]
