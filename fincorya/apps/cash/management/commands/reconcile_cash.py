from django.core.management.base import BaseCommand, CommandError

from apps.cash.models import CashAccount, GlobalCashAccount


class Command(BaseCommand):
    help = "Vérifie que chaque solde de caisse correspond à la dernière écriture de son grand livre."

    def handle(self, *args, **options):
        errors = []
        for account in CashAccount.objects.select_related("currency", "global_account", "agent"):
            latest = account.movements.order_by("-created_at", "-id").first()
            ledger_balance = latest.balance_after if latest else 0
            if account.balance != ledger_balance:
                errors.append(f"Caisse agent #{account.pk}: solde={account.balance}, grand livre={ledger_balance}")
            if account.global_account_id and account.global_account.currency_id != account.currency_id:
                errors.append(f"Caisse agent #{account.pk}: devise incompatible avec la caisse globale")

        for account in GlobalCashAccount.objects.select_related("currency"):
            latest = account.movements.order_by("-created_at", "-id").first()
            ledger_balance = latest.balance_after if latest else 0
            if account.balance != ledger_balance:
                errors.append(f"Caisse globale #{account.pk}: réserve={account.balance}, grand livre={ledger_balance}")
            self.stdout.write(
                f"{account.currency.code}: réserve {account.balance}, caisses agents {account.capital - account.balance}, capital lié {account.capital}"
            )

        if errors:
            raise CommandError("Échec de réconciliation:\n- " + "\n- ".join(errors))
        self.stdout.write(self.style.SUCCESS("Réconciliation des caisses réussie."))
