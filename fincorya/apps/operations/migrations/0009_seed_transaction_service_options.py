from django.db import migrations


def seed_services(apps, schema_editor):
    TransactionServiceOption = apps.get_model("operations", "TransactionServiceOption")
    defaults = [
        ("AIRTEL_MONEY", "Airtel Money"),
        ("VODACOM_MPESA", "Vodacom M-Pesa"),
        ("AFRIMONEY", "Afrimoney"),
        ("TAPTAP_SEND", "Tap Tap Send"),
        ("PAYPAL", "PayPal"),
        ("ORANGE_MONEY", "Orange Money"),
    ]
    for code, label in defaults:
        TransactionServiceOption.objects.get_or_create(code=code, defaults={"label": label, "is_active": True})


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("operations", "0008_transactionserviceoption"),
    ]

    operations = [
        migrations.RunPython(seed_services, noop),
    ]
