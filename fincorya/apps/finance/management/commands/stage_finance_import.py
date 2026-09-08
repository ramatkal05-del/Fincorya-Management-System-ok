import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from apps.accounts.models import User
from apps.finance.imports import stage_import


class Command(BaseCommand):
    help = "Simule ou dépose un JSON dans la préparation d’import, sans écriture définitive."

    def add_arguments(self, parser):
        parser.add_argument("file")
        parser.add_argument("--actor-email", required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        try:
            rows = json.loads(Path(options["file"]).read_text(encoding="utf-8-sig"))
            actor = User.objects.get(email__iexact=options["actor_email"])
            report = stage_import(actor=actor, rows=rows, apply=options["apply"])
        except (ValueError, OSError, User.DoesNotExist, ValidationError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
