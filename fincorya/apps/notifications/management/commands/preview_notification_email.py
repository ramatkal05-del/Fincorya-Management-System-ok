"""Render a notification category's HTML e-mail to a file, with demo data,
so an administrator can check the visual result without sending anything."""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.notifications.emailing import render_email

DEMO_CONTEXTS = {
    "WEEKLY_AGENT_REMINDER": dict(
        subject="La clôture hebdomadaire approche", title="Votre contribution fait vivre FINCORYA au quotidien",
        paragraphs=["La clôture hebdomadaire approche : merci de vérifier que toutes vos opérations de la "
                    "semaine sont enregistrées et complètes avant dimanche 18:00.",
                    "Nous comptons sur votre engagement et vous remercions pour votre travail."],
        cta_label="Vérifier mes opérations", cta_url="#",
    ),
    "WEEKLY_STAKEHOLDER_INVITE": dict(
        subject="Vos informations hebdomadaires sont disponibles", title="Votre espace personnel est à jour",
        paragraphs=["Les informations de la semaine du 01/09/2026 au 06/09/2026 ont été validées et sont "
                    "désormais consultables dans votre espace personnel FINCORYA."],
        cta_label="Consulter mon espace", cta_url="#",
    ),
    "SALARY_INFO": dict(
        subject="Votre salaire — septembre 2026", title="Merci pour votre implication",
        paragraphs=["Merci pour votre implication et le soin apporté à nos clients. Votre travail contribue "
                     "chaque jour au développement de FINCORYA.",
                     "Les informations relatives à votre salaire de septembre 2026 sont disponibles dans votre espace."],
        facts=[{"label": "Période", "value": "Septembre 2026"}, {"label": "Montant validé", "value": "50.00 USD"},
               {"label": "Statut", "value": "Validé — à régler"}],
        cta_label="Consulter mon espace", cta_url="#",
    ),
    "SALARY_PAID": dict(
        subject="Paiement de salaire enregistré — septembre 2026", title="Votre paiement a été enregistré",
        paragraphs=["Le règlement de votre salaire de septembre 2026 vient d'être enregistré."],
        facts=[{"label": "Montant payé", "value": "50.00 USD"}, {"label": "Date de paiement", "value": "30/09/2026 10:00"}],
        cta_label="Consulter mon espace", cta_url="#",
    ),
    "DIVIDEND_INFO": dict(
        subject="Vos dividendes ont été validés", title="Vos dividendes sont validés",
        paragraphs=["Le montant de vos dividendes pour la période ci-dessous a été validé."],
        facts=[{"label": "Période", "value": "01/09/2026 – 30/09/2026"}, {"label": "Montant validé", "value": "120.00 USD"},
               {"label": "Statut", "value": "Validé — dû"}],
        cta_label="Consulter mon espace", cta_url="#",
    ),
    "PARTNER_SHARE_INFO": dict(
        subject="Votre part de commissions a été validée", title="Votre part est validée",
        paragraphs=["Le montant de votre part sur les commissions de la période ci-dessous a été validé."],
        facts=[{"label": "Période", "value": "01/09/2026 – 06/09/2026"}, {"label": "Votre part", "value": "60.00 USD"},
               {"label": "Commission retenue par FINCORYA", "value": "40.00 USD"}, {"label": "Statut", "value": "Validé — dû"}],
        cta_label="Consulter mon espace", cta_url="#",
    ),
    "WELCOME": dict(
        subject="Bienvenue chez FINCORYA — activez votre compte", title="Bienvenue, Jane Doe",
        paragraphs=["Un compte Agent vient d'être créé pour vous sur le système de gestion interne FINCORYA "
                     "(jane.doe@fincorya.local).",
                     "Votre compte vous permet d'enregistrer vos opérations quotidiennes et de suivre votre caisse.",
                     "Pour des raisons de sécurité, aucun mot de passe ne vous est communiqué par e-mail : "
                     "utilisez le lien ci-dessous, personnel et à usage unique, pour définir votre propre mot de passe."],
        facts=[{"label": "Identifiant de connexion", "value": "jane.doe@fincorya.local"}, {"label": "Rôle", "value": "Agent"}],
        cta_label="Activer mon compte et définir mon mot de passe", cta_url="#",
    ),
}


class Command(BaseCommand):
    help = "Prévisualise un modèle d'e-mail FINCORYA (données de démonstration) dans un fichier HTML."

    def add_arguments(self, parser):
        parser.add_argument("category", choices=sorted(DEMO_CONTEXTS))
        parser.add_argument("--out", default=None, help="Chemin du fichier HTML de sortie.")

    def handle(self, *args, **options):
        category = options["category"]
        context = DEMO_CONTEXTS.get(category)
        if not context:
            raise CommandError(f"Aucune donnée de démonstration pour {category}.")
        html_body, _text_body = render_email(**context)
        out = Path(options["out"] or f"preview_{category.lower()}.html")
        out.write_text(html_body, encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Aperçu écrit dans {out.resolve()}"))
