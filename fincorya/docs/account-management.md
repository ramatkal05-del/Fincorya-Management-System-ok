# Gestion des comptes

Les administrateurs ouvrent `/auth/utilisateurs/` puis choisissent un type de
compte. Chaque formulaire collecte uniquement les informations de connexion et
les informations pertinentes pour ce type. Les mots de passe sont valides et
hachés par Django. Les utilisateurs non administrateurs ne peuvent pas accéder
à la liste, créer des comptes ou changer leur statut.

La création ne produit aucun contrat, investissement, participation ou mouvement
financier. Ces dossiers se gèrent séparément dans Finance. Associer un dossier
économique à un utilisateur ne modifie pas son rôle de connexion.

Le formulaire Django `/admin/accounts/user/add/` propose aussi les deux champs
de mot de passe, sans les anciens champs contractuels. Dans les parties
prenantes, le choix initial du type affiche uniquement les sections pertinentes.

L'administration utilise `css/admin-fincorya.css`. Les écritures, comptes et
clôtures du registre y sont consultables ; les mutations financières passent par
l'espace Finance et ses services transactionnels.

Vérifications : tests de création par type, mot de passe utilisable, absence de
création économique implicite, refus des non-administrateurs, rôle imposé par le
parcours, doublons d'e-mail et protection du compte administrateur courant.
