from django.db import models
from django.utils.translation import gettext_lazy as _


class NotificationCategory(models.TextChoices):
    GENERAL = "GENERAL", _("Général (alerte interne)")
    WEEKLY_AGENT_REMINDER = "WEEKLY_AGENT_REMINDER", _("Rappel hebdomadaire agent")
    WEEKLY_STAKEHOLDER_INVITE = "WEEKLY_STAKEHOLDER_INVITE", _("Invitation hebdomadaire partie prenante")
    SALARY_INFO = "SALARY_INFO", _("Salaire validé")
    SALARY_PAID = "SALARY_PAID", _("Salaire payé")
    DIVIDEND_INFO = "DIVIDEND_INFO", _("Dividende validé")
    DIVIDEND_PAID = "DIVIDEND_PAID", _("Dividende payé")
    INVESTOR_DUE = "INVESTOR_DUE", _("Rémunération investisseur due")
    INVESTOR_PAID = "INVESTOR_PAID", _("Rémunération investisseur payée")
    PARTNER_SHARE_INFO = "PARTNER_SHARE_INFO", _("Part partenaire validée")
    PARTNER_SHARE_PAID = "PARTNER_SHARE_PAID", _("Part partenaire payée")
    WELCOME = "WELCOME", _("Bienvenue / activation de compte")
    TEST = "TEST", _("Envoi de test administrateur")


class Notification(models.Model):
    recipient = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="notifications")
    category = models.CharField(max_length=32, choices=NotificationCategory.choices, default=NotificationCategory.GENERAL)
    subject = models.CharField(max_length=180)
    body = models.TextField()
    html_body = models.TextField(blank=True)
    cta_label = models.CharField(max_length=60, blank=True)
    cta_url = models.CharField(max_length=300, blank=True)
    level = models.CharField(max_length=10, choices=[("INFO", "Info"), ("WARNING", "Warning"), ("ERROR", "Error")], default="INFO")
    # Deduplication key: "<category>:<recipient_id>:<period-or-event-id>". Two
    # notifications sharing the same key are the same real-world event, so a
    # scheduler retry or a manual re-run must never create a second row.
    event_key = models.CharField(max_length=180, blank=True, null=True, unique=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "read_at", "-created_at"], name="notification_inbox_idx"),
            models.Index(fields=["category", "-created_at"], name="notification_category_idx"),
        ]

    def __str__(self):
        return f"{self.get_category_display()} → {self.recipient} ({self.subject})"


class NotificationDelivery(models.Model):
    notification = models.OneToOneField(Notification, on_delete=models.CASCADE, related_name="delivery")
    channel = models.CharField(max_length=10, default="EMAIL")
    status = models.CharField(max_length=12, choices=[("PENDING", "Pending"), ("PROCESSING", "Processing"), ("SENT", "Sent"), ("RETRY", "Retry"), ("FAILED", "Failed")], default="PENDING")
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField()
    last_error = models.TextField(blank=True)
    # `accepted_at`: the SMTP/API call returned success (send_mail did not
    # raise). `sent_at` is kept as the historical name for that same instant.
    # Neither implies the mailbox provider actually delivered the message —
    # true delivery confirmation would require provider webhooks, which are
    # not configured; see the deployment notes.
    sent_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["status", "next_attempt_at"], name="notification_queue_idx")]


class NotificationSettings(models.Model):
    """Singleton (pk=1) holding the administrator-configurable notification policy."""

    WEEKDAY_CHOICES = [
        (0, _("Lundi")), (1, _("Mardi")), (2, _("Mercredi")), (3, _("Jeudi")),
        (4, _("Vendredi")), (5, _("Samedi")), (6, _("Dimanche")),
    ]

    weekly_closure_weekday = models.PositiveSmallIntegerField(default=6, choices=WEEKDAY_CHOICES, verbose_name=_("Jour de clôture hebdomadaire"))
    weekly_closure_time = models.TimeField(default="18:00", verbose_name=_("Heure de clôture hebdomadaire"))
    reminder_hours_before = models.PositiveSmallIntegerField(default=2, verbose_name=_("Rappel envoyé N heures avant la clôture"))

    enable_weekly_agent_reminder = models.BooleanField(default=True, verbose_name=_("Rappel hebdomadaire — agents"))
    enable_weekly_stakeholder_invite = models.BooleanField(default=True, verbose_name=_("Invitation hebdomadaire — parties prenantes"))
    enable_salary_notifications = models.BooleanField(default=True, verbose_name=_("Notifications salaires agents"))
    enable_dividend_notifications = models.BooleanField(default=True, verbose_name=_("Notifications dividendes actionnaires"))
    enable_investor_notifications = models.BooleanField(default=True, verbose_name=_("Notifications paiements investisseurs"))
    enable_partner_notifications = models.BooleanField(default=True, verbose_name=_("Notifications parts partenaires"))
    enable_welcome_email = models.BooleanField(default=True, verbose_name=_("E-mail de bienvenue à la création de compte"))

    updated_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Paramètres de notification")
        verbose_name_plural = _("Paramètres de notification")

    def __str__(self):
        return "Paramètres de notification FINCORYA"

    @classmethod
    def load(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj
