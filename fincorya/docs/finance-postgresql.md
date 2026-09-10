# Finance et PostgreSQL local

Le projet utilise PostgreSQL 16 sur `127.0.0.1:55440`, base `fincorya_local`,
utilisateur `fincorya_app`. Le mot de passe est dans `.env` et n'est pas versionné.
L'utilisateur applicatif n'est pas superutilisateur ; le droit CREATEDB sert
aux bases temporaires de tests Django. Le serveur système sur le port 5432
n'a pas été modifié.

## Démarrage et pgAdmin

Depuis le dossier `fincorya`, lancer dans PowerShell :

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\postgres-local.ps1
.\.venv\Scripts\python.exe manage.py runserver
```

Dans pgAdmin, enregistrer un serveur avec l'hôte `127.0.0.1`, le port `55440`,
la base de maintenance `fincorya_local`, l'utilisateur `fincorya_app` et le
mot de passe `DB_PASSWORD` de `.env`.

Les données PostgreSQL sont dans `tmp/postgres-local-16`, à conserver.
La sauvegarde SQLite antérieure au transfert est dans
`tmp/sqlite-before-postgres.sqlite3` ; la configuration précédente est dans
`tmp/env-before-postgres.txt`. Ces fichiers privés restent hors Git.
Les sessions ont été exclues du transfert : une reconnexion peut être nécessaire.

## Contrôles

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test --noinput
.\.venv\Scripts\python.exe manage.py reconcile_cash
```

La CI utilise également PostgreSQL 16. Les tests couvrent la double publication
concurrente, la course entre verrouillage et publication, et les paiements
concurrents d'une même charge. La base de tests est distincte de la base locale.

## Parcours livrés

- `/finance/` : comptes, registre et contrôles.
- `/finance/registre/imports/` : sources préparées, anomalies et revue tracée.
- `/finance/registre/rules/` : règles économiques datées, nouvelles versions
  sans recalcul des anciennes opérations.
- `/finance/parties/<id>/` : positions et opérations de la partie, commissions
  dues et règlements. Une position négative ne devient pas une commission.
- `/finance/registre/expenses/` : charges reconnues, paiements distincts.
- `/finance/registre/profits/` : résultat, trésorerie à la clôture, proposition
  enregistrée par le gestionnaire ou l'administrateur, puis approbation admin.
- `/finance/registre/distributions/` : dettes approuvées puis paiements.

Les journées et semaines fermées interdisent toujours les mouvements de cash
antidatés. La clôture mensuelle peut encore reconnaître ses rémunérations
non payées : l'exception est limitée aux comptes de charge et de dette,
pendant le service de clôture du mois. Elle ne permet jamais d'écrire dans
un mois déjà verrouillé. Les rapports journaliers/hebdomadaires de charges
peuvent ainsi inclure ensuite cette écriture de clôture ; leurs comptages
de cash restent inchangés. Une rémunération modifiée en cours de mois
demande une qualification explicite au lieu d'un prorata inventé.

## Préparation des données confirmées et incertaines

`apps/finance/data/confirmed_brief.json` conserve les montants confirmés
(50 USD pour David, Victoria et Chris ; 40 USD pour Joseph ; aucune
rémunération pour Ruth et Fantiny ; commissions 60/40 ; quatre parts de 25 %)
et les incertitudes historiques séparément. Le fichier ne crée aucune identité,
aucun compte ni aucune écriture.

```powershell
.\.venv\Scripts\python.exe manage.py stage_finance_import apps/finance/data/confirmed_brief.json --actor-email <administrateur>
.\.venv\Scripts\python.exe manage.py stage_finance_import apps/finance/data/confirmed_brief.json --actor-email <administrateur> --apply
```

La commande est idempotente pour une source inchangée. Les dates d'effet sont
volontairement absentes jusqu'à confirmation. Le rattachement aux parties et
aux quatre actionnaires doit être explicite. Les règles datées sont ensuite
saisies dans Finance avec les identifiants validés. La revue d'une source
ne crée jamais une écriture ; toute décision conserve sa justification.

Les classeurs originaux ne sont pas présents dans ce lot : les éléments
préparés proviennent du cahier des charges, pas d'une validation de leurs cellules.
Le rapprochement M-Pesa / Vodacom reste une donnée à confirmer.

## Bascule du moteur financier

La migration technique SQLite → PostgreSQL n'est pas la bascule comptable.
`FINANCE_LEDGER_ENABLED=False` reste en place. Aucun solde d'ouverture du
nouveau registre n'a été publié sur les données locales.

Après revue des sources et validation des règles : arrêter les saisies,
choisir un instant explicite avec fuseau, puis simuler :

```powershell
.\.venv\Scripts\python.exe manage.py migrate_legacy_finance --actor-email <administrateur> --cutover-at <date-ISO-avec-fuseau>
```

Revoir les différences par compte/devise et les anomalies. L'application
requiert le même instant, l'empreinte exacte de la simulation et le moteur
explicitement activé, pendant l'arrêt des saisies :

```powershell
.\.venv\Scripts\python.exe manage.py migrate_legacy_finance --actor-email <administrateur> --cutover-at <meme-instant> --expected-hash <empreinte> --apply
```

La stratégie est une reprise des seuls soldes d'ouverture. Elle ne cumule
pas les mouvements historiques avec ces soldes et refuse une seconde reprise.
