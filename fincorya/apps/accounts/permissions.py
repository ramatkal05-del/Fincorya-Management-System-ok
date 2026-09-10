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
    own_party_only: bool = False   # shareholder / investor / partner: their own situation and requests
    global_read_only: bool = False  # shareholder: global consultation without any modification
    weekly_report: bool = False     # finance manager: global weekly report and monthly preparation


POLICIES = {
    Role.ADMIN: FinancePolicy(view_all=True, prepare=True, approve=True, administer=True, weekly_report=True),
    Role.FINANCE_MANAGER: FinancePolicy(view_all=True, prepare=True, weekly_report=True),
    Role.AGENT: FinancePolicy(own_cash_only=True),
    Role.SHAREHOLDER: FinancePolicy(own_party_only=True, global_read_only=True),
    Role.INVESTOR: FinancePolicy(own_party_only=True),
    Role.PARTNER: FinancePolicy(own_party_only=True),
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


def linked_party(user):
    """The stakeholder record bound to a shareholder / investor / partner user, or None."""
    from apps.stakeholders.models import Stakeholder
    if not finance_policy(user).own_party_only:
        return None
    return Stakeholder.objects.filter(owner=user, is_active=True).first()


def require_party_access(user, stakeholder):
    """Server-side guard: a party-scoped user may only read its own stakeholder record."""
    policy = finance_policy(user)
    if policy.view_all:
        return policy
    party = linked_party(user)
    if party is None or stakeholder is None or party.pk != stakeholder.pk:
        raise PermissionDenied("Vous ne pouvez consulter que votre propre situation.")
    return policy


def can_access_financial_account(user, account):
    policy = finance_policy(user)
    if policy.view_all:
        return True
    return bool(policy.own_cash_only and account.responsible_user_id == user.pk)
