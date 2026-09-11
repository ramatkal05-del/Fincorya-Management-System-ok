# Déploiement FINCORYA sur Render

Ce guide décrit le déploiement de FINCORYA sur [Render](https://render.com)
de bout en bout : infrastructure, configuration, sécurité, et première
configuration administrateur après la mise en service.

> **Aucune donnée métier n'est créée par le déploiement.** L'administrateur
> doit configurer manuellement les comptes, parties prenantes, caisses,
> garanties et soldes d'ouverture après la première mise en production.
> `confirmed_brief.json` est une preuve historique contextuelle, pas une
> source d'import automatique.

---

## 1. Prérequis

| Élément | Détail |
|---|---|
| Compte Render | https://render.com — plan Starter minimum |
| Dépôt Git | `github.com/ramatkal05-del/Fincorya-Management-System-ok` |
| Branche | **`Fincorya`** (pas `main`) |
| Python | 3.11.9 (pinné dans `runtime.txt` à la racine du dépôt) |
| PostgreSQL | Fourni par Render (plan Starter minimum) |
| Polices PDF | Non requises : reportlab utilise Helvetica par défaut sur Linux |

---

## 2. Fichiers de déploiement

| Fichier | Emplacement | Rôle |
|---|---|---|
| `render.yaml` | Racine du dépôt | Blueprint Render (infrastructure as code) |
| `runtime.txt` | Racine du dépôt | Version Python (`python-3.11.9`) — **Render lit ce fichier à la racine** |
| `Procfile` | `fincorya/` | Commandes `web` (gunicorn) et `release` (migrate + create_admin) |
| `build.sh` | `fincorya/` | Script de build (pip install, collectstatic, migrate, create_admin) |
| `requirements.txt` | `fincorya/` | Dépendances Python |
| `.env.example` | `fincorya/` | Modèle de variables d'environnement |

> `runtime.txt` existe **aux deux emplacements** (racine + `fincorya/`) :
> la racine pour Render, `fincorya/` pour Heroku et autres PaaS.

Le `render.yaml` crée automatiquement :

- **Une base de données PostgreSQL** (`fincorya-db`) avec utilisateur `fincorya_app`.
- **Un service web** (`fincorya`) avec gunicorn, 3 workers, timeout 120 s.
- **Un disque persistant** de 10 Go monté sur `/var/data` pour les
  justificatifs, photos, contrats et rapports générés.

---

## 3. Déploiement via Blueprint (recommandé)

### 3.1. Pousser le code sur GitHub

```bash
git push origin Fincorya
```

> La branche de déploiement est **`Fincorya`**, pas `main`.

### 3.2. Importer le Blueprint sur Render

1. Aller sur https://dashboard.render.com
2. Cliquer **New** → **Blueprint**
3. Sélectionner le dépôt `ramatkal05-del/Fincorya-Management-System-ok`
4. **Branch** : `Fincorya`
5. Render détecte `render.yaml` à la racine automatiquement
6. Vérifier les ressources détectées :
   - `fincorya-db` (PostgreSQL)
   - `fincorya` (Web Service)
7. Cliquer **Apply**

### 3.3. Définir les secrets manuels

Dans le dashboard Render, service `fincorya` → **Environment** :

| Variable | Valeur | Notes |
|---|---|---|
| `EMAIL_HOST` | `smtp.gmail.com` ou équivalent | |
| `EMAIL_HOST_USER` | Adresse email d'envoi | |
| `EMAIL_HOST_PASSWORD` | Mot de passe d'application | Gmail : mot de passe d'application, pas le mot de passe normal |
| `ADMIN_PASSWORD` | *(requis à la création)* | Mot de passe secret de l'administrateur initial |

> `DJANGO_SECRET_KEY` est auto-généré par Render (`generateValue: true`).
> Les variables marquées `sync: false` doivent être définies manuellement.

### 3.4. Déploiement initial

Render lance automatiquement :

1. **Détection Python** : lit `runtime.txt` à la racine → Python 3.11.9
2. **Build** (`./build.sh`) :
   - `pip install -r requirements.txt`
   - `python manage.py collectstatic --noinput`
   - `python manage.py migrate --noinput`
   - `python manage.py create_admin` → crée `fincoryagroup@gmail.com`
3. **Pre-deploy** : `python manage.py migrate --noinput`
4. **Start** : `gunicorn` démarre sur le port Render attribué
5. **Health check** sur `/health/` (vérifie la connexion PostgreSQL)

Le déploiement est **prêt** quand le health check passe (statut **Live**).

---

## 4. Déploiement manuel (sans Blueprint)

Si vous préférez configurer manuellement :

### 4.1. Créer la base PostgreSQL

1. **New** → **PostgreSQL**
2. Nom : `fincorya-db`
3. Database : `fincorya`, User : `fincorya_app`
4. Plan : Starter minimum
5. Noter les identifiants fournis (host, port, user, password, database)

### 4.2. Créer le service web

1. **New** → **Web Service**
2. Connecter le dépôt GitHub
3. **Branch** : `Fincorya`
4. **Runtime** : Python 3 (Render lira `runtime.txt` pour la version exacte)
5. **Root Directory** : `fincorya`
6. **Build Command** : `./build.sh`
7. **Start Command** : `gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 3 --timeout 120 --access-logfile - --error-logfile -`
8. **Health Check Path** : `/health/`
9. Plan : Starter minimum

> Ne pas ajouter `--chdir fincorya` : le **Root Directory** place déjà
> Render dans `fincorya/`.

### 4.3. Créer le disque persistant

1. Service `fincorya` → **Disks** → **Add Disk**
2. Nom : `fincorya-private-files`
3. Mount Path : `/var/data`
4. Size : 10 Go

### 4.4. Configurer les variables d'environnement

Reproduire la liste du `render.yaml` (section `envVars`). Les variables
`DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` doivent pointer
vers la base PostgreSQL créée en 4.1.

---

## 5. Variables d'environnement complètes

### Application

| Variable | Production | Description |
|---|---|---|
| `ENVIRONMENT` | `production` | Active les garde-fous (DEBUG=False, PostgreSQL requis, MFA obligatoire) |
| `DEBUG` | `False` | Désactive les pages d'erreur détaillées |
| `DJANGO_SECRET_KEY` | *(auto-généré)* | Clé de signature Django |
| `MFA_ENABLED` | `True` | Obligatoire en production |
| `LOCAL_AUTH_BYPASS` | `False` | Obligatoire en production |
| `TRUST_PROXY_HEADERS` | `True` | Render termine SSL au proxy |
| `BUSINESS_TIME_ZONE` | `Europe/Istanbul` | Fuseau métier pour les clôtures |
| `RENDER_EXTERNAL_HOSTNAME` | *(auto)* | Défini par Render automatiquement |
| `ALLOWED_HOSTS` | `fincorya.com,www.fincorya.com` | Domaines autorisés |
| `CSRF_TRUSTED_ORIGINS` | `https://fincorya.com,https://www.fincorya.com` | Origines CSRF autorisées |

### Base de données

Render fournit la connexion via des variables individuelles (pas `DATABASE_URL`) :

| Variable | Source | Description |
|---|---|---|
| `DB_HOST` | Auto (Blueprint) | Hôte PostgreSQL Render |
| `DB_PORT` | Auto (Blueprint) | Port PostgreSQL (5432) |
| `DB_NAME` | Auto (Blueprint) | Nom de la base (`fincorya`) |
| `DB_USER` | Auto (Blueprint) | Utilisateur (`fincorya_app`) |
| `DB_PASSWORD` | Auto (Blueprint) | Mot de passe PostgreSQL |

> `settings.py` utilise `DB_HOST` en priorité. Si `DB_HOST` est vide,
> il fallback sur `DATABASE_URL` puis SQLite.

### Email

| Variable | Description |
|---|---|
| `DEFAULT_FROM_EMAIL` | Expéditeur des notifications |
| `EMAIL_BACKEND` | `django.core.mail.backends.smtp.EmailBackend` |
| `EMAIL_HOST` | Serveur SMTP |
| `EMAIL_PORT` | 587 (TLS) ou 465 (SSL) |
| `EMAIL_HOST_USER` | Utilisateur SMTP |
| `EMAIL_HOST_PASSWORD` | Mot de passe SMTP |
| `EMAIL_USE_TLS` | `True` |

### Fichiers

| Variable | Production | Description |
|---|---|---|
| `MEDIA_ROOT` | `/var/data/media` | Disque persistant Render |
| `REPORT_RETENTION_DAYS` | `365` | Conservation des exports |

### Moteur financier

| Variable | Production | Description |
|---|---|---|
| `FINANCE_LEDGER_ENABLED` | `False` | Activer **uniquement** après une bascule réconciliée et approuvée |
| `ADMIN_PASSWORD` | *(requis à la création)* | Mot de passe secret de l'administrateur initial |

### Rapports

| Variable | Production | Description |
|---|---|---|
| `FINANCE_WEEK_START` | `0` | 0 = lundi, 6 = dimanche |
| `REPORT_DOWNLOAD_ROLES` | `ADMIN,FINANCE_MANAGER,AGENT` | Rôles autorisés à télécharger |

---

## 6. Vérifications post-déploiement

### 6.1. Health check

```bash
curl https://<votre-app>.onrender.com/health/
# {"status": "ok", "service": "fincorya"}
```

### 6.2. Page de connexion

Visiter `https://<votre-app>.onrender.com/auth/login/` — la page de
connexion FINCORYA doit s'afficher.

### 6.3. Vérifier les migrations

Dans le dashboard Render → service `fincorya` → **Shell** :

```bash
python manage.py showmigrations
```

Toutes les migrations doivent être marquées `[X]`.

### 6.4. Vérifier les statics

```bash
curl -I https://<votre-app>.onrender.com/static/css/base.css
# HTTP/1.1 200 OK
```

### 6.5. Vérifier la version Python

Dans les logs de build Render, la première ligne doit indiquer :

```
==> Using Python version 3.11.9
```

Si Render affiche 3.14.x, vérifier que `runtime.txt` existe **à la racine
du dépôt** (pas seulement dans `fincorya/`).

---

## 7. Création du premier administrateur

Le déploiement crée **automatiquement** l'administrateur initial
(`fincoryagroup@gmail.com`) via la commande `create_admin` exécutée
pendant le build (`build.sh`) et la phase `release` (`Procfile`).

### 7.1. Mot de passe

Avant la première création de l'administrateur `fincoryagroup@gmail.com`,
définir un mot de passe secret dans la variable d'environnement
`ADMIN_PASSWORD` du dashboard Render. Sans cette valeur, la commande échoue
sans créer de compte. Un administrateur existant reste inchangé.

> **Changez ce mot de passe dès la première connexion** via le menu
> profil → Changer le mot de passe.

### 7.2. Vérifier la création

Dans les logs de déploiement Render, vous verrez :

```
Administrateur créé : fincoryagroup@gmail.com (Admin FINCORYA)
Mot de passe par défaut appliqué. Changez-le dès la première connexion.
```

Ou si l'admin existe déjà :

```
L'administrateur fincoryagroup@gmail.com existe déjà — aucune action.
```

### 7.3. Configurer TOTP (MFA obligatoire)

1. Se connecter avec `fincoryagroup@gmail.com` et le mot de passe défini
2. Au premier login, Django-OTP demande de configurer un appareil TOTP
3. Scanner le QR code avec une application (Google Authenticator, Authy, etc.)
4. Valider le code à 6 chiffres

### 7.4. Changer le mot de passe (recommandé)

Après la première connexion :

1. Menu profil → **Changer le mot de passe**
2. Saisir le mot de passe actuel et un nouveau mot de passe fort
3. La session reste active après le changement

---

## 8. Configuration administrateur initiale

Après la création de l'administrateur, suivre **cet ordre exact** dans
l'interface FINCORYA :

### Étape 1 — Devises et taux de change
- **Finance** → **Services** : créer les services (Western Union, MoneyGram, etc.)
- Créer les devises (USD, EUR, TRY, etc.)
- Définir les taux de change vers USD

### Étape 2 — Caisses
- **Finance** → **Comptes financiers** : créer la caisse globale administrateur
- **Caisses** : allouer des caisses aux agents

### Étape 3 — Parties prenantes
- **Finance** → **Parties prenantes** : créer les stakeholders réels
  - Investisseurs, partenaires, actionnaires
- Renseigner les dépôts, garanties, pourcentages contractuels

### Étape 4 — Apports et soldes d'ouverture
- **Finance** → **Apports** : enregistrer les apports réels
- Les soldes d'ouverture ne sont **pas** des apports : les distinguer

### Étape 5 — Tarifs
- Définir les grilles tarifaires par service et devise

### Étape 6 — Politiques de distribution
- **Finance** → **Distribution** : créer les politiques
- Sélectionner **explicitement** les bénéficiaires (pas de fallback)

### Étape 7 — Activer le moteur financier (optionnel)
- Préparer et réconcilier la bascule comptable
- Approuver la migration
- Activer `FINANCE_LEDGER_ENABLED=True` dans Render

> **Ne pas activer `FINANCE_LEDGER_ENABLED` avant d'avoir réconcilié
> et approuvé la bascule.** Le moteur refuse toute écriture sans bascule
> approuvée.

---

## 9. Sécurité en production

| Contrôle | État |
|---|---|
| `DEBUG=False` | Garde-fou dans `settings.py` (refuse de démarrer sinon) |
| `MFA_ENABLED=True` | Obligatoire en production |
| `LOCAL_AUTH_BYPASS=False` | Obligatoire en production |
| PostgreSQL | Obligatoire en production (garde-fou dans `settings.py`) |
| Cookies Secure | `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` |
| HSTS | 1 an, sous-domaines, preload |
| X-Frame-Options | `DENY` |
| CSP | `default-src 'self'` |
| Proxy SSL | `SECURE_PROXY_SSL_HEADER` configuré pour Render |
| Fichiers privés | Servis via vues authentifiées (pas de `/media/` public) |

---

## 10. Disque persistant et fichiers

Le disque Render (`/var/data`, 10 Go) stocke :

- `/var/data/media/private/contributions/` — justificatifs d'apports
- `/var/data/media/private/expenses/` — justificatifs de charges
- `/var/data/media/private/reports/` — exports PDF/XLSX/CSV générés
- `/var/data/media/private/contracts/` — documents contractuels
- `/var/data/media/agents/` — photos d'agents

La variable `MEDIA_ROOT` est définie sur `/var/data/media` dans le
`render.yaml`. Ces fichiers sont servis **uniquement** via des vues
authentifiées (`receipt_download`, `profile_photo`, `report_download`) —
jamais via `/media/` public.

### Sauvegarde

Render ne sauvegarde pas automatiquement les disques. Planifier :

1. Sauvegarde PostgreSQL : Render Starter inclut des snapshots quotidiens
2. Sauvegarde des fichiers : script `rsync` ou S3 sync régulier

---

## 11. Mises à jour

### Déploiement continu

Si `autoDeploy: true` (défaut dans `render.yaml`), chaque `push` sur la
branche **`Fincorya`** déclenche :

1. Build (`build.sh`)
2. Migrations (`manage.py migrate`)
3. Redémarrage gunicorn

### Rollback

Render conserve les déploiements précédents. Dashboard → service →
**Manual Deploy** → sélectionner un commit antérieur.

---

## 12. Surveillance

### Logs

Dashboard Render → service → **Logs**. Filtres disponibles :
- Build logs
- Deploy logs
- Runtime logs

### Health check

`/health/` vérifie la connexion PostgreSQL. Si la base est injoignable,
Render marque le service **Unhealthy** et redémarre automatiquement.

### Métriques

Render Starter inclut : CPU, mémoire, temps de réponse moyen.

---

## 13. Limites connues

| Limite | Détail |
|---|---|
| Polices PDF | Arial Narrow indisponible sur Linux ; reportlab utilise Helvetica |
| Disque 10 Go | Augmenter si les rapports générés dépassent la capacité |
| Workers gunicorn | 3 par défaut ; augmenter sur plan supérieur |
| Email SMTP | Configurer un service SMTP (Gmail, SendGrid, etc.) |
| `confirmed_brief.json` | Contexte historique uniquement, pas d'import automatique |
| Version Python | `runtime.txt` doit être à la racine du dépôt pour Render |

---

## 14. Commandes utiles (shell Render)

```bash
# État des migrations
python manage.py showmigrations

# Recréer l'admin initial (idempotent)
python manage.py create_admin

# Collecter les statics manuellement
python manage.py collectstatic --noinput

# Vérifier la configuration
python manage.py check --deploy

# Réconcilier les caisses
python manage.py reconcile_cash

# Générer des aperçus de rapports
python manage.py generate_report_previews
```

---

## 15. Dépannage

### Render utilise Python 3.14 au lieu de 3.11

`runtime.txt` doit exister **à la racine du dépôt** (pas seulement dans
`fincorya/`). Vérifier :

```bash
cat runtime.txt
# python-3.11.9
```

Si absent à la racine, le créer et pousser.

### `./build.sh: No such file or directory`

1. Vérifier que `build.sh` est dans `fincorya/` et est exécutable
2. Vérifier que **Root Directory** = `fincorya` dans Render
3. Vérifier que la branche déployée est `Fincorya` (pas `main`)

### `connection refused` ou `role does not exist`

Les variables `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `DB_PORT`
ne sont pas définies. Vérifier que le service est lié à la base
PostgreSQL `fincorya-db` dans le dashboard Render.

### Health check échoue

1. Vérifier que les migrations sont appliquées (logs de build)
2. Vérifier que `DATABASE_URL` ou `DB_HOST` pointe vers Render PostgreSQL
3. Tester dans le shell : `python manage.py check`
