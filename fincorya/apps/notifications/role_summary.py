"""Short, honest description of what each role can actually do in FINCORYA —
used only in the welcome e-mail. Kept in sync with `apps.accounts.models.Role`;
never mention a screen or capability the role does not have access to."""
from apps.accounts.models import Role

_SUMMARIES = {
    Role.ADMIN: "Votre compte donne accès à l'administration complète : comptes utilisateurs, clôtures financières et rapports.",
    Role.FINANCE_MANAGER: "Votre compte donne accès aux écrans financiers : opérations, charges, clôtures de période et rapports.",
    Role.AGENT: "Votre compte vous permet d'enregistrer vos opérations quotidiennes et de suivre votre caisse.",
    Role.PARTNER: "Votre compte vous permet de consulter votre espace personnel : vos opérations attribuées et votre part de commissions.",
    Role.INVESTOR: "Votre compte vous permet de consulter votre espace personnel : vos investissements et vos rémunérations validées.",
    Role.SHAREHOLDER: "Votre compte vous permet de consulter votre espace personnel : vos participations et vos dividendes validés.",
}


def role_capabilities(role):
    return _SUMMARIES.get(role, "Votre compte vous donne accès aux fonctions correspondant à votre rôle dans FINCORYA.")
