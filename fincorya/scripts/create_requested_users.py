import os
import sys
import django

# Setup Django environment
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.accounts.models import User, Role
from apps.stakeholders.models import Stakeholder, StakeholderType

PASSWORD = "FincoryaDemo2026!"

users_to_create = [
    # Agents (4)
    ("david.matamba@fincorya.local", "David", "Matamba", Role.AGENT, None),
    ("ruth.ngomo@fincorya.local", "Ruth", "Ngomo", Role.AGENT, None),
    ("victorine.tshonda@fincorya.local", "Victorine", "Tshonda", Role.AGENT, None),
    ("christ.kapia@fincorya.local", "Christ", "Kapia", Role.AGENT, None),
    # Actionnaires (4)
    ("meschack.bukasa@fincorya.local", "Meschack", "Bukasa", Role.SHAREHOLDER, StakeholderType.SHAREHOLDER),
    ("isaka@fincorya.local", "Isaka", "", Role.SHAREHOLDER, StakeholderType.SHAREHOLDER),
    ("jenovich.mpoto@fincorya.local", "Jenovich", "Mpoto", Role.SHAREHOLDER, StakeholderType.SHAREHOLDER),
    ("glodie.kongolo@fincorya.local", "Glodie", "Kongolo", Role.SHAREHOLDER, StakeholderType.SHAREHOLDER),
    # Investisseur (1)
    ("joseph.omari@fincorya.local", "Joseph", "Omari", Role.INVESTOR, StakeholderType.INVESTOR),
    # Partenaire (1)
    ("fantiny@fincorya.local", "Fantiny", "", Role.PARTNER, StakeholderType.PARTNER),
]

for email, first_name, last_name, role, stk_type in users_to_create:
    user, created = User.objects.update_or_create(
        email=email,
        defaults={
            "first_name": first_name,
            "last_name": last_name,
            "role": role,
            "is_active": True,
            "is_staff": False,
            "is_superuser": False,
        }
    )
    user.set_password(PASSWORD)
    user.save()
    status_user = "Créé" if created else "Mis à jour"
    print(f"Compte Utilisateur: {user.email} [{user.get_role_display()}] - {status_user}")

    if stk_type:
        name = f"{first_name} {last_name}".strip()
        stk, stk_created = Stakeholder.objects.update_or_create(
            owner=user,
            defaults={
                "name": name,
                "type": stk_type,
                "email": email,
                "is_active": True,
            }
        )
        status_stk = "Créé" if stk_created else "Mis à jour"
        print(f"  -> Profil Partie Prenante: {stk.name} [{stk.get_type_display()}] - {status_stk}")

print("\nTous les comptes et profils ont été créés et configurés avec succès.")
