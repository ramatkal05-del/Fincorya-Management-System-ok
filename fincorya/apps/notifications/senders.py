"""Domain-aware notification senders.

Each function below turns a real, already-validated system event (an
`Expense`, a `Distribution`, a locked `FinancialPeriod`, an issued
`AccountActivationToken`...) into a branded e-mail through `notify()`. None
of them compute or alter a financial amount — they only read what the
finance/profits/expenses services already produced and decided.
"""
from django.urls import reverse

from .emailing import absolute_url
from .models import NotificationCategory
from .services import notify


def _settings():
    from .models import NotificationSettings
    return NotificationSettings.load()


# --------------------------------------------------------------------- agents


def send_weekly_agent_reminder(*, agent, closure_at):
    settings = _settings()
    if not settings.enable_weekly_agent_reminder:
        return None
    event_key = f"weekly-agent-reminder:{agent.pk}:{closure_at.date().isoformat()}"
    closure_label = closure_at.strftime("%A %d/%m/%Y à %H:%M")
    return notify(
        recipient=agent, category=NotificationCategory.WEEKLY_AGENT_REMINDER, event_key=event_key,
        subject="La clôture hebdomadaire approche",
        title="Votre contribution fait vivre FINCORYA au quotidien",
        paragraphs=[
            f"La clôture hebdomadaire approche : merci de vérifier que toutes vos opérations de la semaine "
            f"sont enregistrées et complètes avant {closure_label}.",
            "Nous comptons sur votre engagement et vous remercions pour votre travail.",
        ],
        cta_label="Vérifier mes opérations", cta_url=absolute_url(reverse("operations:list")),
    )


# --------------------------------------------------------- weekly stakeholder invites


def notify_period_closed(*, period):
    """Called once a `FinancialPeriod` is locked (see `apps.finance.closing.close_period`).

    Sends the "your weekly information is available" invite to every active
    partner/investor/shareholder with a login account, and — for partners —
    the "your commission share is validated" info, computed from the same
    read-only aggregation (`stakeholders.services`) already used elsewhere;
    no amount is recalculated here.
    """
    from apps.pricing.models import Currency
    from apps.stakeholders.models import PartnerOperation, Stakeholder, StakeholderType
    from apps.stakeholders.services import fincorya_partner_share, partner_commission_base

    stakeholders = Stakeholder.objects.filter(
        type__in=[StakeholderType.PARTNER, StakeholderType.INVESTOR, StakeholderType.SHAREHOLDER],
        is_active=True, owner__isnull=False, owner__is_active=True,
    ).select_related("owner")
    period_label = f"{period.start_date:%d/%m/%Y} – {period.end_date:%d/%m/%Y}"
    for stakeholder in stakeholders:
        send_weekly_stakeholder_invite(user=stakeholder.owner, period=period)
        if stakeholder.type != StakeholderType.PARTNER:
            continue
        currencies = PartnerOperation.objects.filter(
            stakeholder=stakeholder, operation__status="COMPLETED",
            operation__created_at__date__gte=period.start_date, operation__created_at__date__lte=period.end_date,
        ).values_list("operation__currency", flat=True).distinct()
        for currency in Currency.objects.filter(pk__in=currencies):
            base = partner_commission_base(stakeholder, currency=currency, start_date=period.start_date, end_date=period.end_date)
            if base <= 0:
                continue
            share = fincorya_partner_share(stakeholder, currency=currency, start_date=period.start_date, end_date=period.end_date)
            send_partner_share_info(stakeholder=stakeholder, period_label=period_label,
                                     share_amount=share, retained_amount=base - share, currency_code=currency.code)


def send_weekly_stakeholder_invite(*, user, period):
    settings = _settings()
    if not settings.enable_weekly_stakeholder_invite:
        return None
    event_key = f"weekly-invite:{user.pk}:{period.pk}"
    return notify(
        recipient=user, category=NotificationCategory.WEEKLY_STAKEHOLDER_INVITE, event_key=event_key,
        subject="Vos informations hebdomadaires sont disponibles",
        title="Votre espace personnel est à jour",
        paragraphs=[
            f"Les informations de la semaine du {period.start_date:%d/%m/%Y} au {period.end_date:%d/%m/%Y} "
            f"ont été validées et sont désormais consultables dans votre espace personnel FINCORYA.",
        ],
        cta_label="Consulter mon espace", cta_url=absolute_url(reverse("finance:party_space")),
    )


# --------------------------------------------------------------------- salaries


def send_salary_info(*, expense):
    settings = _settings()
    if not settings.enable_salary_notifications or not expense.agent_id:
        return None
    event_key = f"salary-info:{expense.pk}"
    return notify(
        recipient=expense.agent, category=NotificationCategory.SALARY_INFO, event_key=event_key,
        subject=f"Votre salaire — {expense.incurred_on:%B %Y}",
        title="Merci pour votre implication",
        paragraphs=[
            "Merci pour votre implication et le soin apporté à nos clients. Votre travail contribue chaque "
            "jour au développement de FINCORYA.",
            f"Les informations relatives à votre salaire de {expense.incurred_on:%B %Y} sont disponibles "
            f"dans votre espace, avec le statut « validé, en attente de règlement ».",
        ],
        facts=[{"label": "Période", "value": expense.incurred_on.strftime("%B %Y")},
               {"label": "Montant validé", "value": f"{expense.amount} {expense.currency.code}"},
               {"label": "Statut", "value": "Validé — à régler"}],
        cta_label="Consulter mon espace", cta_url=absolute_url(reverse("accounts:profile")),
    )


def send_salary_paid(*, payment):
    settings = _settings()
    expense = payment.expense
    if not settings.enable_salary_notifications or not expense.agent_id:
        return None
    event_key = f"salary-paid:{payment.pk}"
    return notify(
        recipient=expense.agent, category=NotificationCategory.SALARY_PAID, event_key=event_key,
        subject=f"Paiement de salaire enregistré — {expense.incurred_on:%B %Y}",
        title="Votre paiement a été enregistré",
        paragraphs=[f"Le règlement de votre salaire de {expense.incurred_on:%B %Y} vient d'être enregistré. "
                     f"Merci encore pour votre engagement au quotidien envers FINCORYA."],
        facts=[{"label": "Montant payé", "value": f"{payment.amount} {expense.currency.code}"},
               {"label": "Date de paiement", "value": payment.paid_at.strftime("%d/%m/%Y %H:%M")}],
        cta_label="Consulter mon espace", cta_url=absolute_url(reverse("accounts:profile")),
    )


# ------------------------------------------------------------------- dividends


def send_dividend_info(*, distribution):
    settings = _settings()
    owner = distribution.stakeholder.owner
    if not settings.enable_dividend_notifications or owner is None:
        return None
    event_key = f"dividend-info:{distribution.pk}"
    period = distribution.allocation.period
    return notify(
        recipient=owner, category=NotificationCategory.DIVIDEND_INFO, event_key=event_key,
        subject="Vos dividendes ont été validés",
        title="Vos dividendes sont validés",
        paragraphs=["Le montant de vos dividendes pour la période ci-dessous a été validé conformément à la "
                     "politique de distribution FINCORYA. Le règlement fera l'objet d'une confirmation séparée."],
        facts=[{"label": "Période", "value": f"{period.start_date:%d/%m/%Y} – {period.end_date:%d/%m/%Y}"},
               {"label": "Montant validé", "value": f"{distribution.amount} {period.currency.code}"},
               {"label": "Statut", "value": "Validé — dû"}],
        cta_label="Consulter mon espace", cta_url=absolute_url(reverse("finance:party_space")),
    )


def send_dividend_paid(*, distribution):
    settings = _settings()
    owner = distribution.stakeholder.owner
    if not settings.enable_dividend_notifications or owner is None:
        return None
    event_key = f"dividend-paid:{distribution.pk}"
    period = distribution.allocation.period
    return notify(
        recipient=owner, category=NotificationCategory.DIVIDEND_PAID, event_key=event_key,
        subject="Paiement de dividendes enregistré",
        title="Votre paiement a été enregistré",
        paragraphs=["Le règlement de vos dividendes pour la période ci-dessous vient d'être enregistré."],
        facts=[{"label": "Période", "value": f"{period.start_date:%d/%m/%Y} – {period.end_date:%d/%m/%Y}"},
               {"label": "Montant payé", "value": f"{distribution.amount} {period.currency.code}"},
               {"label": "Date de paiement", "value": distribution.paid_at.strftime("%d/%m/%Y %H:%M") if distribution.paid_at else "—"}],
        cta_label="Consulter mon espace", cta_url=absolute_url(reverse("finance:party_space")),
    )


# ------------------------------------------------------------------ investors


def send_investor_due_from_expense(*, expense):
    settings = _settings()
    stakeholder = expense.stakeholder
    owner = stakeholder.owner if stakeholder else None
    if not settings.enable_investor_notifications or owner is None:
        return None
    event_key = f"investor-due:expense:{expense.pk}"
    return notify(
        recipient=owner, category=NotificationCategory.INVESTOR_DUE, event_key=event_key,
        subject="Votre rémunération d'investisseur a été validée",
        title="Votre rémunération contractuelle est validée",
        paragraphs=["Conformément aux conditions et à l'échéance prévues par votre contrat, le montant "
                     "ci-dessous a été validé. Le règlement fera l'objet d'une confirmation séparée."],
        facts=[{"label": "Échéance", "value": expense.incurred_on.strftime("%d/%m/%Y")},
               {"label": "Montant validé", "value": f"{expense.amount} {expense.currency.code}"},
               {"label": "Statut", "value": "Validé — dû"}],
        cta_label="Consulter mon espace", cta_url=absolute_url(reverse("finance:party_space")),
    )


def send_investor_paid_from_expense(*, payment):
    settings = _settings()
    expense = payment.expense
    stakeholder = expense.stakeholder
    owner = stakeholder.owner if stakeholder else None
    if not settings.enable_investor_notifications or owner is None:
        return None
    event_key = f"investor-paid:expense:{payment.pk}"
    return notify(
        recipient=owner, category=NotificationCategory.INVESTOR_PAID, event_key=event_key,
        subject="Paiement de votre rémunération d'investisseur enregistré",
        title="Votre paiement a été enregistré",
        paragraphs=["Le règlement de votre rémunération contractuelle vient d'être enregistré."],
        facts=[{"label": "Montant payé", "value": f"{payment.amount} {expense.currency.code}"},
               {"label": "Date de paiement", "value": payment.paid_at.strftime("%d/%m/%Y %H:%M")}],
        cta_label="Consulter mon espace", cta_url=absolute_url(reverse("finance:party_space")),
    )


def send_investor_due_from_distribution(*, distribution):
    settings = _settings()
    owner = distribution.stakeholder.owner
    if not settings.enable_investor_notifications or owner is None:
        return None
    event_key = f"investor-due:distribution:{distribution.pk}"
    period = distribution.allocation.period
    return notify(
        recipient=owner, category=NotificationCategory.INVESTOR_DUE, event_key=event_key,
        subject="Votre part de bénéfice investisseur a été validée",
        title="Votre part est validée",
        paragraphs=["Le montant ci-dessous a été validé selon la politique de distribution FINCORYA."],
        facts=[{"label": "Période", "value": f"{period.start_date:%d/%m/%Y} – {period.end_date:%d/%m/%Y}"},
               {"label": "Montant validé", "value": f"{distribution.amount} {period.currency.code}"},
               {"label": "Statut", "value": "Validé — dû"}],
        cta_label="Consulter mon espace", cta_url=absolute_url(reverse("finance:party_space")),
    )


def send_investor_paid_from_distribution(*, distribution):
    settings = _settings()
    owner = distribution.stakeholder.owner
    if not settings.enable_investor_notifications or owner is None:
        return None
    event_key = f"investor-paid:distribution:{distribution.pk}"
    period = distribution.allocation.period
    return notify(
        recipient=owner, category=NotificationCategory.INVESTOR_PAID, event_key=event_key,
        subject="Paiement de votre part investisseur enregistré",
        title="Votre paiement a été enregistré",
        paragraphs=["Le règlement de votre part investisseur vient d'être enregistré."],
        facts=[{"label": "Montant payé", "value": f"{distribution.amount} {period.currency.code}"},
               {"label": "Date de paiement", "value": distribution.paid_at.strftime("%d/%m/%Y %H:%M") if distribution.paid_at else "—"}],
        cta_label="Consulter mon espace", cta_url=absolute_url(reverse("finance:party_space")),
    )


# -------------------------------------------------------------------- partners


def send_partner_share_info(*, stakeholder, period_label, share_amount, retained_amount, currency_code):
    settings = _settings()
    owner = stakeholder.owner
    if not settings.enable_partner_notifications or owner is None or share_amount <= 0:
        return None
    event_key = f"partner-share-info:{stakeholder.pk}:{period_label}"
    return notify(
        recipient=owner, category=NotificationCategory.PARTNER_SHARE_INFO, event_key=event_key,
        subject="Votre part de commissions a été validée",
        title="Votre part est validée",
        paragraphs=["Le montant de votre part sur les commissions de la période ci-dessous a été validé. "
                     "Le règlement fera l'objet d'une confirmation séparée."],
        facts=[{"label": "Période", "value": period_label},
               {"label": "Votre part", "value": f"{share_amount} {currency_code}"},
               {"label": "Commission retenue par FINCORYA", "value": f"{retained_amount} {currency_code}"},
               {"label": "Statut", "value": "Validé — dû"}],
        cta_label="Consulter mon espace", cta_url=absolute_url(reverse("finance:party_space")),
    )


def send_partner_share_paid(*, stakeholder, amount, currency_code, paid_at):
    settings = _settings()
    owner = stakeholder.owner
    if not settings.enable_partner_notifications or owner is None:
        return None
    event_key = f"partner-share-paid:{stakeholder.pk}:{paid_at.isoformat()}:{amount}"
    return notify(
        recipient=owner, category=NotificationCategory.PARTNER_SHARE_PAID, event_key=event_key,
        subject="Paiement de votre part de commissions enregistré",
        title="Votre paiement a été enregistré",
        paragraphs=["Le règlement de votre part de commissions vient d'être enregistré."],
        facts=[{"label": "Montant payé", "value": f"{amount} {currency_code}"},
               {"label": "Date de paiement", "value": paid_at.strftime("%d/%m/%Y %H:%M")}],
        cta_label="Consulter mon espace", cta_url=absolute_url(reverse("finance:party_space")),
    )


# --------------------------------------------------------------------- welcome


def send_welcome_email(*, user, raw_token):
    settings = _settings()
    if not settings.enable_welcome_email:
        return None
    from .role_summary import role_capabilities
    activation_path = reverse("accounts:activate", kwargs={"user_id": user.pk, "token": raw_token})
    return notify(
        recipient=user, category=NotificationCategory.WELCOME, event_key=None,  # a resend must be able to send again
        subject="Bienvenue chez FINCORYA — activez votre compte",
        title=f"Bienvenue, {user.get_full_name() or user.email}",
        paragraphs=[
            f"Un compte {user.get_role_display()} vient d'être créé pour vous sur le système de gestion "
            f"interne FINCORYA ({user.email}).",
            role_capabilities(user.role),
            "Pour des raisons de sécurité, aucun mot de passe ne vous est communiqué par e-mail : "
            "utilisez le lien ci-dessous, personnel et à usage unique, pour définir votre propre mot de passe. "
            "Ce lien expire après 72 heures.",
        ],
        facts=[{"label": "Identifiant de connexion", "value": user.email},
               {"label": "Rôle", "value": user.get_role_display()},
               {"label": "Adresse du système", "value": absolute_url("/")}],
        cta_label="Activer mon compte et définir mon mot de passe", cta_url=absolute_url(activation_path),
    )
