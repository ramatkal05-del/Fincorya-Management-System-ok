#!/usr/bin/env bash
# Build script Render — exécuté à la racine du dépôt.
set -euo pipefail

echo "=== FINCORYA build ==="

# 1. Installer les dépendances Python
pip install --upgrade pip
pip install -r fincorya/requirements.txt

# 2. Travailler dans le sous-dossier fincorya
cd fincorya

# 3. Collecter les fichiers statiques (WhiteNoise les servira)
python manage.py collectstatic --noinput

# 4. Appliquer les migrations de base de données
python manage.py migrate --noinput

# 5. Créer l'administrateur initial (idempotent)
python manage.py create_admin

echo "=== Build terminé ==="
