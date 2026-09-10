from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounts.permissions import require_finance_access
from apps.audit.services import record
from apps.stakeholders.models import Stakeholder
from .locking import ledger_atomic, save_internal
from .models import DistributionMode, DistributionPolicy, DistributionPolicyShare, FinancialPeriod


@ledger_atomic
def create_distribution_policy(*, actor, mode, effective_from, shares, notes=""):
    require_finance_access(actor, "administer")
    if mode not in DistributionMode.values or not shares:
        raise ValidationError("Choisissez un mode et sélectionnez explicitement les bénéficiaires.")
    if Stakeholder.objects.filter(pk__in=shares, type="SHAREHOLDER", is_active=True).count() != len(shares):
        raise ValidationError("Sélectionnez des actionnaires actifs existants.")
    if FinancialPeriod.objects.filter(period_type="MONTH", status="LOCKED", end_date__gte=effective_from).exists():
        raise ValidationError("La date d’effet doit être postérieure aux mois déjà clôturés.")
    shares = {pk: Decimal(str(value)) for pk, value in shares.items()}
    if any(value < 0 or value > 100 for value in shares.values()):
        raise ValidationError("Pourcentage invalide.")
    if mode != DistributionMode.CAPITAL_PROPORTIONAL and sum(shares.values()) != 100:
        raise ValidationError("Les parts sélectionnées doivent totaliser 100 %.")
    policy = DistributionPolicy(mode=mode, effective_from=effective_from, notes=notes, created_by=actor)
    policy.full_clean()
    policy.save()
    for pk, percent in shares.items():
        save_internal(DistributionPolicyShare(policy=policy, stakeholder_id=pk, percent=percent))
    record(actor=actor, action="DISTRIBUTION_POLICY_CREATE", instance=policy,
           after={"mode": mode, "effective_from": str(effective_from), "shares": {str(k): str(v) for k, v in shares.items()}})
    return policy
