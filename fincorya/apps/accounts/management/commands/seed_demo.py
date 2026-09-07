from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Role, User
from apps.cash.models import CashAccount, GlobalCashAccount
from apps.pricing.models import Currency


DEMO_PASSWORD = "FincoryaDemo2026!"
DEMO_USERS = (
    ("admin@fincorya.local", "Amina", "Admin", Role.ADMIN),
    ("agent@fincorya.local", "Grâce", "Agent", Role.AGENT),
    ("partenaire@fincorya.local", "David", "Partenaire", Role.PARTNER),
    ("investisseur@fincorya.local", "Sarah", "Investisseur", Role.INVESTOR),
    ("actionnaire@fincorya.local", "Patrick", "Actionnaire", Role.SHAREHOLDER),
)


class Command(BaseCommand):
    help = "Crée un jeu de comptes et de caisses FINCORYA pour la démonstration locale."

    @transaction.atomic
    def handle(self, *args, **options):
        users = {}
        for email, first_name, last_name, role in DEMO_USERS:
            user, _ = User.objects.update_or_create(
                email=email,
                defaults={
                    "first_name": first_name,
                    "last_name": last_name,
                    "role": role,
                    "is_active": True,
                    "is_staff": role == Role.ADMIN,
                    "is_superuser": role == Role.ADMIN,
                },
            )
            user.set_password(DEMO_PASSWORD)
            user.save()
            users[role] = user

        agent = users[Role.AGENT]
        opening_balances = {
            "USD": Decimal("2500.00"),
            "EUR": Decimal("1500.00"),
            "TRY": Decimal("50000.00"),
            "GBP": Decimal("1000.00"),
            "CDF": Decimal("5000000.00"),
        }
        for code, balance in opening_balances.items():
            currency, _ = Currency.objects.get_or_create(code=code)
            global_account, global_created = GlobalCashAccount.objects.get_or_create(
                currency=currency,
                defaults={"administrator": users[Role.ADMIN]},
            )
            account, account_created = CashAccount.objects.get_or_create(
                agent=agent,
                currency=currency,
                defaults={"global_account": global_account},
            )
            if account.global_account_id != global_account.pk:
                account.global_account = global_account
                account.save(update_fields=["global_account"])
            if global_created and account_created:
                from apps.cash.models import MovementDirection
                from apps.cash.services import adjust_global_cash, allocate_cash
                adjust_global_cash(
                    global_account_id=global_account.pk, direction=MovementDirection.IN,
                    amount=balance, adjusted_by=users[Role.ADMIN], note="Capital initial de démonstration",
                )
                allocate_cash(
                    account_id=account.pk, amount=balance, allocated_by=users[Role.ADMIN],
                    note="Allocation initiale de démonstration",
                )

        self.stdout.write(self.style.SUCCESS("Données de démonstration FINCORYA prêtes."))
        self.stdout.write(f"Mot de passe commun : {DEMO_PASSWORD}")
        for email, _, _, role in DEMO_USERS:
            self.stdout.write(f"- {role}: {email}")
