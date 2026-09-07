from django.db import migrations


def backfill_legacy_cash_ledgers(apps, schema_editor):
    CashAccount = apps.get_model("cash", "CashAccount")
    CashMovement = apps.get_model("cash", "CashMovement")
    for account in CashAccount.objects.exclude(balance=0).iterator():
        if CashMovement.objects.filter(account_id=account.pk).exists():
            continue
        CashMovement.objects.create(
            account_id=account.pk,
            direction="IN",
            movement_type="OPENING",
            amount=account.balance,
            balance_after=account.balance,
            note="Solde existant repris lors de la sécurisation du grand livre",
            created_by_id=account.allocated_by_id,
        )


class Migration(migrations.Migration):
    dependencies = [("cash", "0008_cashmovement_cash_account_date_idx_and_more")]

    operations = [migrations.RunPython(backfill_legacy_cash_ledgers, migrations.RunPython.noop)]
