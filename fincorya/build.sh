#!/usr/bin/env bash
# Build script Render — exécuté à chaque déploiement.
# Render démarre déjà dans rootDir (fincorya/) grâce à render.yaml.
set -euo pipefail

echo "=== FINCORYA build ==="

# 1. Installer les dépendances Python
pip install --upgrade pip
pip install -r requirements.txt

# 2. Collecter les fichiers statiques (WhiteNoise les servira)
python manage.py collectstatic --noinput

# 3. Appliquer les migrations de base de données
python manage.py migrate --noinput

# 4. Créer l'administrateur initial (idempotent)
python manage.py create_admin

echo "=== Build terminé ==="
