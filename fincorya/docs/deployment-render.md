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
| Dépôt Git | Le code doit être poussé sur GitHub ou GitLab |
| Python | 3.11.9 (pinné dans `runtime.txt`) |
| PostgreSQL | Fourni par Render (plan Starter minimum) |
| Polices PDF | Non requises : reportlab utilise Helvetica par défaut sur Linux |

---

## 2. Fichiers de déploiement fournis

| Fichier | Rôle |
|---|---|
| `render.yaml` | Blueprint Render (infrastructure as code) |
| `Procfile` | Commandes `web` (gunicorn) et `release` (migrations + collectstatic) |
| `build.sh` | Script de build (pip install, collectstatic, migrate) |
| `runtime.txt` | Version Python (`python-3.11.9`) |
| `requirements.txt` | Dépendances Python |
| `.env.example` | Modèle de variables d'environnement |

Le `render.yaml` crée automatiquement :

- **Une base de données PostgreSQL** (`fincorya-db`) avec utilisateur `fincorya_app`.
- **Un service web** (`fincorya`) avec gunicorn, 3 workers, timeout 120 s.
- **Un disque persistant** de 5 Go monté sur `/opt/render/media` pour les
  justificatifs, photos, contrats et rapports générés.

---

## 3. Déploiement via Blueprint (recommandé)

### 3.1. Pousser le code sur GitHub

```bash
git push origin main
```

### 3.2. Importer le Blueprint sur Render

1. Aller sur https://dashboard.render.com
2. Cliquer **New** → **Blueprint**
3. Sélectionner le dépôt GitHub contenant FINCORYA
4. Render détecte `render.yaml` automatiquement
5. Vérifier les ressources détectées :
   - `fincorya-db` (PostgreSQL)
   - `fincorya` (Web Service)
6. Cliquer **Apply**

### 3.3. Définir les secrets manuels

Dans le dashboard Render, service `fincorya` → **Environment** :

| Variable | Valeur | Notes |
|---|---|---|
| `DJANGO_SECRET_KEY` | Clé aléatoire longue | `python -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `EMAIL_HOST` | `smtp.gmail.com` ou équivalent | |
| `EMAIL_HOST_USER` | Adresse email d'envoi | |
| `EMAIL_HOST_PASSWORD` | Mot de passe d'application | Gmail : mot de passe d'application, pas le mot de passe normal |

> Les variables marquées `sync: false` dans `render.yaml` doivent être
> définies manuellement. Les autres sont pré-remplies par le Blueprint.

### 3.4. Déploiement initial

Render lance automatiquement :

1. `build.sh` → `pip install`, `collectstatic`, `migrate`
2. `gunicorn` démarre sur le port Render attribué
3. Health check sur `/health/` (vérifie la connexion PostgreSQL)

Le déploiement est **prêt** quand le health check passe (statut **Live**).

---

## 4. Déploiement manuel (sans Blueprint)

Si vous préférez configurer manuellement :

### 4.1. Créer la base PostgreSQL

1. **New** → **PostgreSQL**
2. Nom : `fincorya-db`
3. Database : `fincorya`, User : `fincorya_app`
4. Plan : Starter minimum
5. Noter la **Internal Database URL** fournie

### 4.2. Créer le service web

1. **New** → **Web Service**
2. Connecter le dépôt GitHub
3. Runtime : **Python 3**
4. Build Command : `./build.sh`
5. Start Command : `gunicorn config.wsgi:application --chdir fincorya --bind 0.0.0.0:$PORT --workers 3 --timeout 120`
6. Health Check Path : `/health/`
7. Plan : Starter minimum

### 4.3. Créer le disque persistant

1. Service `fincorya` → **Disks** → **Add Disk**
2. Nom : `fincorya-media`
3. Mount Path : `/opt/render/media`
4. Size : 5 Go

### 4.4. Configurer les variables d'environnement

Reproduire la liste du `render.yaml` (section `envVars`).

---

## 5. Variables d'environnement complètes

### Application

| Variable | Production | Description |
|---|---|---|
| `ENVIRONMENT` | `production` | Active les garde-fous (DEBUG=False, PostgreSQL requis, MFA obligatoire) |
| `DEBUG` | `False` | Désactive les pages d'erreur détaillées |
| `DJANGO_SECRET_KEY` | *(secret)* | Clé de signature Django |
| `MFA_ENABLED` | `True` | Obligatoire en production |
| `LOCAL_AUTH_BYPASS` | `False` | Obligatoire en production |
| `BUSINESS_TIME_ZONE` | `Europe/Istanbul` | Fuseau métier pour les clôtures |
| `RENDER_EXTERNAL_HOSTNAME` | *(auto)* | Défini par Render automatiquement |

### Base de données

| Variable | Source | Description |
|---|---|---|
| `DATABASE_URL` | Auto (Blueprint) | URL de connexion PostgreSQL fournie par Render |

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
| `MEDIA_ROOT` | `/opt/render/media` | Disque persistant Render |
| `REPORT_RETENTION_DAYS` | `365` | Conservation des exports |

### Moteur financier

| Variable | Production | Description |
|---|---|---|
| `FINANCE_LEDGER_ENABLED` | `False` | Activer **uniquement** après une bascule réconciliée et approuvée |

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

---

## 7. Création du premier administrateur

Le déploiement ne crée **aucun utilisateur**. Après le premier déploiement :

### 7.1. Via le shell Render

1. Dashboard Render → service `fincorya` → **Shell**
2. Exécuter :

```bash
python manage.py shell
```

```python
from apps.accounts.models import Role, User
admin = User.objects.create_user(
    email="admin@fincorya.com",
    password="MOT_DE_PASSE_FORT",
    role=Role.ADMIN,
    first_name="Prénom",
    last_name="Nom",
)
admin.is_active = True
admin.is_staff = True
admin.is_superuser = True
admin.save()
```

### 7.2. Configurer TOTP (MFA obligatoire)

1. Se connecter avec l'administrateur
2. Au premier login, Django-OTP demande de configurer un appareil TOTP
3. Scanner le QR code avec une application (Google Authenticator, Authy, etc.)
4. Valider le code à 6 chiffres

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

Le disque Render (`/opt/render/media`, 5 Go) stocke :

- `private/contributions/` — justificatifs d'apports
- `private/expenses/` — justificatifs de charges
- `private/reports/` — exports PDF/XLSX/CSV générés
- `private/contracts/` — documents contractuels
- `agents/` — photos d'agents

Ces fichiers sont servis **uniquement** via des vues authentifiées
(`receipt_download`, `profile_photo`, `report_download`) — jamais via
`/media/` public.

### Sauvegarde

Render ne sauvegarde pas automatiquement les disques. Planifier :

1. Sauvegarde PostgreSQL : Render Starter inclut des snapshots quotidiens
2. Sauvegarde des fichiers : script `rsync` ou S3 sync régulier

---

## 11. Mises à jour

### Déploiement continu

Si `autoDeploy: true` (défaut dans `render.yaml`), chaque `push` sur
`main` déclenche :

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
| Disque 5 Go | Augmenter si les rapports générés dépassent la capacité |
| Workers gunicorn | 3 par défaut ; augmenter sur plan supérieur |
| Email SMTP | Configurer un service SMTP (Gmail, SendGrid, etc.) |
| `confirmed_brief.json` | Contexte historique uniquement, pas d'import automatique |

---

## 14. Commandes utiles (shell Render)

```bash
# État des migrations
python manage.py showmigrations

# Créer un superutilisateur
python manage.py shell

# Collecter les statics manuellement
python manage.py collectstatic --noinput

# Vérifier la configuration
python manage.py check --deploy

# Réconcilier les caisses
python manage.py reconcile_cash

# Générer des aperçus de rapports
python manage.py generate_report_previews
```
