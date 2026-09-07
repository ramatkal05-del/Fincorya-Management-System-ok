# FINCORYA

FINCORYA est un monolithe Django modulaire destiné à sécuriser le parcours
complet : allocation administrateur → opération agent → rapport → bénéfice.

## Socle technique

- Python 3.11 et Django 5.2 LTS
- PostgreSQL en production, SQLite pour le développement local
- services métier transactionnels avec verrouillage `select_for_update`
- écritures de caisse et événements d'audit immuables
- interface serveur prévue avec Django, HTMX, Alpine.js et Tailwind CSS
- déploiement Render via `render.yaml`

## Interface FINCORYA

L'interface applique le système visuel institutionnel FINCORYA : émeraude,
vert profond, ivoire, graphite et or utilisé avec retenue. Le logo officiel
est servi sans modification depuis `static_src/img/fincorya-group-logo-fr.svg`.
Les écrans utilisent Plus Jakarta Sans, des composants responsives, des icônes
linéaires cohérentes, des états accessibles et une navigation adaptée au rôle.

## Domaines FINCORYA

| App | Responsabilité |
|---|---|
| `accounts` | administrateurs, agents, partenaires, investisseurs, actionnaires et TOTP |
| `pricing` | devises, taux versionnés et grilles tarifaires |
| `cash` | caisses multidevises allouées aux agents et mouvements immuables |
| `operations` | transferts, retraits, paiements et corrections |
| `expenses` | dépenses, charges et salaires saisis par l'administrateur |
| `stakeholders` | partenaires, investisseurs et actionnaires |
| `profits` | bénéfice net, allocations 60/20/20 et distributions |
| `contracts` | contrats privés et versions de documents |
| `reports` | exports et instantanés |
| `notifications` | notifications internes |
| `audit` | journal append-only des mutations critiques |

## Démarrage local

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python manage.py migrate
python manage.py seed_prd_tariffs
python manage.py seed_demo
python manage.py runserver
```

En développement uniquement, `LOCAL_AUTH_BYPASS=True` ouvre directement une
session avec `LOCAL_AUTH_EMAIL`. Conservez toujours cette option à `False` en
production.

Le health check est disponible sur `/health/`. Les secrets et paramètres
PostgreSQL sont fournis par variables d'environnement en production.
Les dépendances JavaScript sont servies localement et les réponses applicatives
appliquent une politique CSP restrictive.

## Authentification

La connexion utilise l'adresse e-mail et impose une deuxième étape TOTP.
Lors de la première connexion, FINCORYA affiche un QR code à scanner dans une
application d'authentification. Après confirmation, huit codes de récupération
à usage unique sont générés et stockés uniquement sous forme hachée.
Après les connexions suivantes, l'utilisateur peut valider le second facteur
avec l'application TOTP, un code e-mail temporaire de cinq minutes ou, en
dernier recours, un code de récupération. Les envois et validations sont
limités et inscrits dans le journal d'audit. En développement, le backend
e-mail `console` affiche le message dans le terminal ; configurez les variables
SMTP pour une livraison réelle.

Parcours disponibles :

- `/auth/login/` : connexion par e-mail ;
- `/auth/verify/` : vérification TOTP ou code de récupération ;
- `/auth/totp/setup/` : activation initiale du TOTP ;
- `/auth/profil/` : modification du profil personnel (coordonnées, langue et photo) ;
- `/auth/profil/mot-de-passe/` : changement sécurisé du mot de passe avec confirmation de l'ancien ;
- `/` : tableau de bord protégé par MFA.

## État du MVP

Le MVP exécutable couvre l'authentification MFA, les opérations idempotentes,
les caisses multidevises allouées exclusivement par l'administrateur, les
dépenses et salaires, les bénéfices 60/20/20, les notifications persistantes
et les exports privés PDF/XLSX/CSV. L'administrateur crée les utilisateurs,
gère les taux et tarifs, et génère les rapports mensuels consolidés. Les agents
ne clôturent pas leur caisse : les transferts la créditent et les retraits la
débitent dans la limite du solde disponible.

Avant une mise en production financière réelle, exécutez aussi la recette sur
une base PostgreSQL dédiée (notamment les scénarios concurrents), configurez
les sauvegardes du disque privé et réalisez une validation métier/UAT signée.

## Règles financières du PRD

- Les tranches tarifaires sont choisies après conversion du montant en USD.
- Un transfert envoyé crédite la caisse du montant augmenté des frais.
- Un retrait payé débite le montant diminué des frais, une seule fois.
- Au-delà de 5 000 USD, les frais et leur justification exigent un administrateur.
- Une caisse doit être allouée et activée par l'administrateur avant toute opération.
- Toute variation de la caisse globale produit une écriture immuable avec solde avant/après et justification.
- Le bénéfice déduit les dépenses, charges et salaires effectivement saisis et validés par l'administrateur.
- Les enveloppes 60/20/20 et les parts individuelles sont calculées depuis un instantané.
- La part FINCORYA sur les commissions partenaires est de 40 %.
- Les rapports PDF, XLSX et CSV utilisent exactement le même jeu de données et les mêmes totaux.

## Déploiement Render

Le blueprint racine crée PostgreSQL, le service web, un disque privé persistant
et le worker de notifications. Après création, renseignez les paramètres SMTP
et copiez exactement la même valeur `DJANGO_SECRET_KEY` vers le worker. Vérifiez
ensuite `/health/`, créez le premier administrateur avec `python manage.py
createsuperuser`, puis chargez les tarifs avec `python manage.py
seed_prd_tariffs`.

Configurez `DEFAULT_FROM_EMAIL` avec un domaine SMTP vérifié. Pour imposer Arial
Narrow dans les PDF Linux, placez les fichiers licenciés `arialn.ttf` et
`arialnb.ttf` dans un dossier persistant et renseignez `REPORT_FONT_DIR`. Les
migrations sont exécutées par la phase `preDeployCommand`, séparément du build.
La durée de conservation des exports est pilotée par `REPORT_RETENTION_DAYS` ;
planifiez `python manage.py purge_expired_reports` quotidiennement.
Exécutez aussi `python manage.py reconcile_cash` après chaque déploiement et
avant une clôture comptable ; la commande échoue si un solde ne correspond pas
à la dernière écriture de son grand livre.

Avant chaque livraison, exécutez `python manage.py check --deploy`,
`python manage.py makemigrations --check --dry-run` et `python manage.py test`.
Le workflow CI du dépôt automatise ces contrôles sur chaque push et pull request.
