"""
Fincorya settings — minimal scaffold matching section 3/4/10 of the MVP plan.
Split into per-environment files (base/dev/prod) before go-live; kept as one
file here so the business-logic port stays easy to review.
"""
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
from decouple import AutoConfig

BASE_DIR = Path(__file__).resolve().parent.parent
env = AutoConfig(search_path=BASE_DIR)
as_bool = lambda value: str(value).strip().lower() in {"1", "true", "yes", "on"}

DEBUG = env("DEBUG", default=True, cast=as_bool)
ENVIRONMENT = env("ENVIRONMENT", default="development").strip().lower()
SECRET_KEY = env("DJANGO_SECRET_KEY", default="")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY est obligatoire lorsque DEBUG=False.")
    SECRET_KEY = "fincorya-local-only-2026-change-before-production-9x7p-4k2m-8q5v"
ALLOWED_HOSTS = [host.strip() for host in env("ALLOWED_HOSTS", default="localhost,127.0.0.1").split(",") if host.strip()]
ALLOWED_HOSTS.extend(host for host in ("fincorya.com", "www.fincorya.com") if host not in ALLOWED_HOSTS)
RENDER_EXTERNAL_HOSTNAME = env("RENDER_EXTERNAL_HOSTNAME", default="")
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in env("CSRF_TRUSTED_ORIGINS", default="").split(",") if origin.strip()]
if RENDER_EXTERNAL_HOSTNAME:
    CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_EXTERNAL_HOSTNAME}")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "django_htmx",

    # Fincorya domain apps (section 4)
    "apps.accounts",
    "apps.pricing",
    "apps.cash",
    "apps.finance",
    "apps.operations",
    "apps.audit",
    "apps.stakeholders",
    "apps.expenses",
    "apps.profits",
    "apps.contracts",
    "apps.reports",
    "apps.notifications",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "config.middleware.SecurityHeadersMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",  # FR/EN, section 5.1 / 13
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.audit.middleware.AuditRequestContextMiddleware",
    "apps.accounts.middleware.LocalAuthenticationBypassMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "apps.accounts.middleware.AdminMFAMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.accounts.context_processors.security_settings",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DB_HOST = env("DB_HOST", default="")
DATABASE_URL = env("DATABASE_URL", default="")
if DB_HOST:
    # Variables individuelles (développement local, serveur dédié).
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME", default="fincorya"), "USER": env("DB_USER", default="fincorya"),
        "PASSWORD": env("DB_PASSWORD", default=""), "HOST": DB_HOST,
        "PORT": env("DB_PORT", default="5432"),
        "CONN_MAX_AGE": 60,
    }}
elif DATABASE_URL:
    # Render et autres PaaS fournissent une URL de connexion unique.
    from urllib.parse import urlparse
    parsed = urlparse(DATABASE_URL)
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql" if parsed.scheme.startswith("postgres") else "django.db.backends.sqlite3",
        "NAME": parsed.path.lstrip("/"),
        "USER": parsed.username or "",
        "PASSWORD": parsed.password or "",
        "HOST": parsed.hostname or "",
        "PORT": str(parsed.port or 5432),
        "CONN_MAX_AGE": 60,
    }}
else:
    # Local/test fallback. Production refuses to start with DEBUG enabled below.
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

AUTH_USER_MODEL = "accounts.User"
MFA_ENABLED = env("MFA_ENABLED", default=True, cast=as_bool)
LOCAL_AUTH_BYPASS = env("LOCAL_AUTH_BYPASS", default=False, cast=as_bool)
LOCAL_AUTH_EMAIL = env("LOCAL_AUTH_EMAIL", default="admin@fincorya.local")
TRUST_PROXY_HEADERS = env("TRUST_PROXY_HEADERS", default=False, cast=as_bool)
if ENVIRONMENT == "production":
    if DEBUG or LOCAL_AUTH_BYPASS or not MFA_ENABLED:
        raise ImproperlyConfigured("Production exige DEBUG=False, LOCAL_AUTH_BYPASS=False et MFA_ENABLED=True.")
    if DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
        raise ImproperlyConfigured("PostgreSQL est obligatoire en production.")
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"
# Absolute origin used to build links/images inside outgoing e-mails (activation
# links, "consulter mon espace" buttons, logo). Relative URLs are meaningless
# once rendered in a mail client, so this must point at the real deployment.
SITE_URL = env("SITE_URL", default="http://localhost:8000").rstrip("/")
# Budget-friendly alternative to a dedicated Render Worker/Cron: run the
# notification delivery loop and the weekly-reminder check inside the web
# process itself. See apps/notifications/background.py.
RUN_INLINE_NOTIFICATION_WORKER = env("RUN_INLINE_NOTIFICATION_WORKER", default=False, cast=as_bool)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="FINCORYA <noreply@fincorya.local>")
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env("EMAIL_PORT", default=587, cast=int)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env("EMAIL_USE_TLS", default=True, cast=as_bool)
EMAIL_TIMEOUT = 15

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Section 5.1: UTC in DB, Europe/Istanbul for business-date logic (closures).
LANGUAGE_CODE = "fr"
TIME_ZONE = "UTC"
BUSINESS_TIME_ZONE = env("BUSINESS_TIME_ZONE", default="Europe/Istanbul")
USE_I18N = True
USE_TZ = True
LANGUAGES = [("fr", "Français"), ("en", "English")]
LOCALE_PATHS = [BASE_DIR / "locale"]

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "static"
STATICFILES_DIRS = [BASE_DIR / "static_src"]
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
MEDIA_ROOT = Path(env("MEDIA_ROOT", default=str(BASE_DIR / "media")))
if not MEDIA_ROOT.is_absolute():
    MEDIA_ROOT = BASE_DIR / MEDIA_ROOT
MEDIA_URL = "/media/"
REPORT_FONT_DIR = env("REPORT_FONT_DIR", default="")
REPORT_RETENTION_DAYS = env("REPORT_RETENTION_DAYS", default=365, cast=int)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Section 10: cookies Secure/HttpOnly/SameSite, HTTPS forcé en production.
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
# CSRF_COOKIE_HTTPONLY must remain False (Django default) so htmx/JS can read
# the token from the cookie and send it in X-CSRFToken. Setting it True
# breaks all non-form AJAX requests silently.
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = "Lax"
if ENVIRONMENT == "production":
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_REFERRER_POLICY = "same-origin"
    X_FRAME_OPTIONS = "DENY"
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

FINANCE_LEDGER_ENABLED = env("FINANCE_LEDGER_ENABLED", default=False, cast=as_bool)
# Mot de passe de l'administrateur initial (créé par create_admin au déploiement).
ADMIN_PASSWORD = env("ADMIN_PASSWORD", default="")
# Reporting conventions: week start (0 = lundi … 6 = dimanche) and roles allowed to download files.
FINANCE_WEEK_START = env("FINANCE_WEEK_START", default=0, cast=int)
REPORT_DOWNLOAD_ROLES = [role.strip() for role in env("REPORT_DOWNLOAD_ROLES", default="ADMIN,FINANCE_MANAGER,AGENT").split(",") if role.strip()]
