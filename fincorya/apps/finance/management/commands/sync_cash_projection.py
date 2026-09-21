from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.models import User
from apps.finance.events import counterpart, line
from apps.finance.models import AccountType, FinancialAccount
from apps.finance.services import ledger_balance, prepare_batch, post_batch


class Command(BaseCommand):
    help = "Recale le grand livre finance sur les soldes des caisses legacy divergentes."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Publie les écritures de régularisation.")
        parser.add_argument("--actor", help="E-mail de l'administrateur signataire (requis avec --apply).")

    def handle(self, *args, **options):
        divergent = []
        for account in FinancialAccount.objects.select_related("legacy_cash_account", "legacy_global_account", "currency"):
            legacy = account.legacy_cash_account or account.legacy_global_account
            if legacy is None:
                continue
            diff = legacy.balance - ledger_balance(account)
            if diff:
                divergent.append((account, legacy, diff))
        if not divergent:
            self.stdout.write(self.style.SUCCESS("Aucune divergence : grand livre et caisses sont synchrones."))
            return
        for account, legacy, diff in divergent:
            self.stdout.write(f"{account.code}: legacy={legacy.balance} grand livre={ledger_balance(account)} écart={diff}")
        if not options["apply"]:
            self.stdout.write("Relancez avec --apply --actor <email> pour publier les régularisations.")
            return
        if not options["actor"]:
            raise CommandError("--actor <email> est requis avec --apply.")
        actor = User.objects.get(email=options["actor"], is_active=True)
        for account, legacy, diff in divergent:
            cash_side = "DEBIT" if diff > 0 else "CREDIT"
            adjust = counterpart(account.currency, AccountType.ADJUSTMENT)
            batch = prepare_batch(
                actor=actor, idempotency_key=f"projection-sync-{account.pk}-{diff}",
                event_type="MANUAL", effective_at=timezone.now(),
                description=f"Synchronisation projection caisse {account.code} (écart {diff})",
                lines=[line(account, cash_side, abs(diff)),
                       line(adjust, "CREDIT" if cash_side == "DEBIT" else "DEBIT", abs(diff))])
            post_batch(batch_id=batch.pk, actor=actor)
            remaining = legacy.balance - ledger_balance(account)
            if remaining:
                raise CommandError(f"{account.code}: divergence persistante après régularisation ({remaining}).")
            self.stdout.write(self.style.SUCCESS(f"{account.code}: régularisé ({diff})."))
