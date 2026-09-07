from django.db import models

class Notification(models.Model):
    recipient = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="notifications")
    subject = models.CharField(max_length=180)
    body = models.TextField()
    level = models.CharField(max_length=10, choices=[("INFO", "Info"), ("WARNING", "Warning"), ("ERROR", "Error")], default="INFO")
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "read_at", "-created_at"], name="notification_inbox_idx")]


class NotificationDelivery(models.Model):
    notification = models.OneToOneField(Notification, on_delete=models.CASCADE, related_name="delivery")
    channel = models.CharField(max_length=10, default="EMAIL")
    status = models.CharField(max_length=12, choices=[("PENDING", "Pending"), ("PROCESSING", "Processing"), ("SENT", "Sent"), ("RETRY", "Retry"), ("FAILED", "Failed")], default="PENDING")
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField()
    last_error = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["status", "next_attempt_at"], name="notification_queue_idx")]
