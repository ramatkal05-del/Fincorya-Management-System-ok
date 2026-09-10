#!/usr/bin/env bash
# Build script Render — exécuté à chaque déploiement.
set -euo pipefail

echo "=== FINCORYA build ==="

# 1. Installer les dépendances Python
pip install --upgrade pip
pip install -r requirements.txt

# 2. Préparer le répertoire de travail
cd fincorya

# 3. Collecter les fichiers statiques (WhiteNoise les servira)
python manage.py collectstatic --noinput

# 4. Appliquer les migrations de base de données
python manage.py migrate --noinput

# 5. Créer l'administrateur initial (idempotent)
python manage.py create_admin

echo "=== Build terminé ==="
