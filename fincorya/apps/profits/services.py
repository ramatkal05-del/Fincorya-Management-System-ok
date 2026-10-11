"""Compatibility entry points for retired profit allocation scripts.

New results and distributions are produced by apps.finance.closing with dated
policies and immutable snapshots. Historical records remain readable.
"""
from django.core.exceptions import PermissionDenied, ValidationError
from apps.accounts.models import Role


def _require_current_workflow(actor):
    if actor.role != Role.ADMIN:
        raise PermissionDenied("Seul un administrateur peut valider une distribution.")
    raise ValidationError(
        "Le partage historique 60/20/20 est retiré. Utilisez la clôture mensuelle Finance, "
        "puis la proposition et la validation de distribution selon la politique datée. "
        "Les distributions historiques restent inchangées."
    )


def calculate_profit_period(*, period_id, actor):
    _require_current_workflow(actor)


def allocate_individual_distributions(*, period_id, actor):
    _require_current_workflow(actor)
