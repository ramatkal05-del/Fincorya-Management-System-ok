# Generated manually: add the intermediate "received, pending validation" state for internal transfers.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0004_fundcontribution_evidence_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="internaltransfer",
            name="received_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.RESTRICT, related_name="transfers_received", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="internaltransfer",
            name="received_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="internaltransfer",
            name="status",
            field=models.CharField(choices=[("INITIATED", "En transit"), ("RECEIVED", "Reçu, en attente de validation"), ("CONFIRMED", "Confirmé"), ("CANCELLED", "Annulé")], default="INITIATED", max_length=10),
        ),
    ]
