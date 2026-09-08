"""Central role-based policy used by views and business services."""
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied

from .models import Role


@dataclass(frozen=True)
class FinancePolicy:
    view_all: bool = False
    prepare: bool = False
    approve: bool = False
    administer: bool = False
    own_cash_only: bool = False


POLICIES = {
    Role.ADMIN: FinancePolicy(True, True, True, True, False),
    Role.FINANCE_MANAGER: FinancePolicy(True, True, False, False, False),
    Role.AGENT: FinancePolicy(False, False, False, False, True),
}


def finance_policy(user):
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return FinancePolicy()
    return POLICIES.get(user.role, FinancePolicy())


def require_finance_access(user, capability="view_all"):
    policy = finance_policy(user)
    if not getattr(policy, capability, False):
        raise PermissionDenied("Vous ne disposez pas de cette autorisation financière.")
    return policy


def can_access_financial_account(user, account):
    policy = finance_policy(user)
    if policy.view_all:
        return True
    return bool(policy.own_cash_only and account.responsible_user_id == user.pk)
