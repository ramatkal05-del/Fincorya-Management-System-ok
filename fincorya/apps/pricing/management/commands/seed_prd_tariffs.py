from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction
from apps.pricing.models import Currency, TariffSchedule, TariffTier


TIERS = [
    ("0.01", "40.00", "5.00"), ("40.01", "100.00", "8.00"),
    ("100.01", "200.00", "15.00"), ("200.01", "300.00", "20.00"),
    ("300.01", "400.00", "26.00"), ("400.01", "600.00", "30.00"),
    ("600.01", "800.00", "35.00"), ("800.01", "1000.00", "40.00"),
    ("1000.01", "1200.00", "45.00"), ("1200.01", "1500.00", "64.00"),
    ("1500.01", "1800.00", "70.00"), ("1800.01", "2000.00", "86.00"),
    ("2000.01", "2400.00", "100.00"), ("2400.01", "2800.00", "115.00"),
    ("2800.01", "3200.00", "130.00"), ("3200.01", "3600.00", "150.00"),
    ("3600.01", "4000.00", "165.00"), ("4000.01", "4500.00", "175.00"),
    ("4500.01", "5000.00", "185.00"),
]


class Command(BaseCommand):
    help = "Installe la grille tarifaire USD officielle du PRD FINCORYA."

    @transaction.atomic
    def handle(self, *args, **options):
        usd, _ = Currency.objects.get_or_create(code="USD")
        schedule, _ = TariffSchedule.objects.get_or_create(name="FINCORYA PRD V1", defaults={"currency": usd})
        schedule.currency, schedule.is_published = usd, True
        schedule.save(update_fields=["currency", "is_published"])
        schedule.tiers.all().delete()
        TariffTier.objects.bulk_create([
            TariffTier(schedule=schedule, min_amount=Decimal(low), max_amount=Decimal(high), fixed_fee=Decimal(fee))
            for low, high, fee in TIERS
        ])
        self.stdout.write(self.style.SUCCESS(f"Grille FINCORYA PRD V1 installée : {len(TIERS)} tranches."))
