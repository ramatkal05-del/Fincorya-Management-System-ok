"""
Identity domain.

Defines FINCORYA users and roles from the MVP plan.
"""
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.contrib.auth.hashers import check_password, make_password
from decimal import Decimal
from django.db import models
from django.utils.translation import gettext_lazy as _


class Role(models.TextChoices):
    ADMIN = "ADMIN", _("Administrateur")
    AGENT = "AGENT", _("Agent")
    PARTNER = "PARTNER", _("Partenaire")
    INVESTOR = "INVESTOR", _("Investisseur")
    SHAREHOLDER = "SHAREHOLDER", _("Actionnaire")


class UserManager(BaseUserManager):
    """FINCORYA email-based user manager."""

    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError(_("L'adresse e-mail est obligatoire."))
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        # FINCORYA administrators must be able to use Django's administration
        # console from the product dashboard.
        extra_fields.setdefault("is_staff", extra_fields.get("role") == Role.ADMIN)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", Role.ADMIN)
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """
    Custom user, defined from the first migration as required by section 10
    of the MVP plan. `username` is kept (unique, auto-derived) so Django admin
    keeps working, but authentication is by email.
    """
    username = models.CharField(max_length=150, unique=True, blank=True)
    email = models.EmailField(_("Adresse e-mail"), unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.AGENT, verbose_name=_("Rôle"))
    phone = models.CharField(max_length=20, blank=True, verbose_name=_("Téléphone"))
    city = models.CharField(max_length=120, blank=True, verbose_name=_("Ville"))
    photo = models.ImageField(upload_to="agents/%Y/%m/", blank=True, null=True, verbose_name=_("Photo"))
    language = models.CharField(max_length=5, default="fr", choices=[("fr", "Français"), ("en", "English")])
    agent_started_on = models.DateField(null=True, blank=True, verbose_name=_("Début d'activité agent"))
    agent_ended_on = models.DateField(null=True, blank=True, verbose_name=_("Fin d'activité agent"))
    monthly_salary_usd = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("50.00"))

    # TOTP — section 10: application d'authentification + codes de récupération
    # à usage unique. In production, wire this through django-otp
    # (otp_totp.TOTPDevice) rather than storing the secret on the user row;
    # these fields are kept only as the minimal scaffold contract.
    totp_enabled = models.BooleanField(default=False)
    totp_confirmed_at = models.DateTimeField(null=True, blank=True)

    is_active = models.BooleanField(default=True, verbose_name=_("Compte actif"))

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = _("Utilisateur")
        verbose_name_plural = _("Utilisateurs")
        constraints = [
            models.CheckConstraint(condition=models.Q(monthly_salary_usd__gte=0), name="user_salary_non_negative"),
            models.CheckConstraint(condition=models.Q(agent_ended_on__isnull=True) | models.Q(agent_started_on__isnull=True) | models.Q(agent_ended_on__gte=models.F("agent_started_on")), name="agent_employment_dates_valid"),
        ]

    def save(self, *args, **kwargs):
        if not self.username:
            self.username = self.email
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_full_name() or self.email} ({self.get_role_display()})"


class RecoveryCode(models.Model):
    """Single-use hashed TOTP recovery code."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="recovery_codes")
    code_hash = models.CharField(max_length=128)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def set_code(self, raw_code):
        self.code_hash = make_password(raw_code)

    def matches(self, raw_code):
        return self.used_at is None and check_password(raw_code, self.code_hash)


class AuthThrottle(models.Model):
    """Database-backed authentication throttle shared by all web workers."""
    key_hash = models.CharField(max_length=64)
    action = models.CharField(max_length=32)
    attempts = models.PositiveSmallIntegerField(default=0)
    window_started = models.DateTimeField()
    blocked_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["key_hash", "action"], name="unique_auth_throttle_key")]
