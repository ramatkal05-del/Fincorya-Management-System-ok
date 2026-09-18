# Guide d'utilisation du compte FINCORYA

Ce guide explique, pour chaque rôle, comment se connecter, quels scénarios suivre au quotidien et **quelles sont les limites strictes de son compte**. Il complète `account-management.md` (création des comptes par l'admin) et `finance-workflows.md` (logique comptable détaillée).

Principe général : **chacun ne voit et n'agit que dans son périmètre**. Toute tentative d'accès hors périmètre (autre caisse, autre partie prenante, page réservée à l'admin…) est bloquée côté serveur, pas seulement masquée à l'écran.

## 1. Connexion et sécurité du compte (tous les rôles)

**Scénario — première connexion**
1. Recevoir l'e-mail et le mot de passe provisoire de l'administrateur → se connecter sur `/auth/login/`.
2. Être redirigé automatiquement vers `/auth/totp/setup/` : scanner le QR code avec une application d'authentification (Google Authenticator, Authy…) et saisir le code à 6 chiffres.
3. Noter et conserver en lieu sûr les **10 codes de récupération** affichés une seule fois (`/auth/recovery-codes/`) : ils ne réapparaîtront jamais.
4. Compléter son profil (`/auth/profil/`) et changer son mot de passe si souhaité (`/auth/profil/mot-de-passe/`).

**Scénario — connexion courante**
1. E-mail + mot de passe sur `/auth/login/`.
2. Code TOTP sur `/auth/verify/`, ou clic sur « Recevoir un code par e-mail » si le téléphone n'est pas disponible, ou en dernier recours un code de récupération à usage unique.

**Scénario — problème d'accès**
- Mot de passe oublié, code de récupération épuisé, téléphone perdu : contacter un administrateur, seul habilité à réinitialiser ou désactiver un compte (`/auth/utilisateurs/`).

**Limites communes à tous les comptes**
- Aucune auto-inscription : un compte n'existe que si un administrateur l'a créé.
- Pas d'accès aux fonctionnalités sans MFA activé (sauf pendant la configuration initiale du TOTP).
- Trop de tentatives de connexion, d'envoi de code e-mail ou de vérification déclenchent un blocage temporaire.
- Impossible de changer son propre rôle ou celui d'un autre utilisateur : seul l'admin le fait.

## 2. Administrateur

**Son espace** : `/auth/utilisateurs/` (comptes), `/finance/` (grand livre complet), `/rapports/centre/` (tous les rapports), tableau de bord global (`/`).

**Scénario A — créer un nouveau compte agent**
1. `/auth/utilisateurs/` → « Créer » → type « Agent » → renseigner e-mail, nom, téléphone.
2. Le mot de passe provisoire est généré/haché automatiquement ; aucun compte financier n'est créé en même temps (pas de caisse, pas de contrat).
3. Ouvrir ensuite sa caisse séparément dans Finance › Comptes financiers (`/finance/caisses-agent/nouvelle/`) puis y allouer des fonds depuis la caisse globale.

**Scénario B — onboarding d'une partie prenante (actionnaire / investisseur / partenaire)**
1. Finance › Parties prenantes › Créer une partie (`/finance/parties/nouvelle/`) : type, devise, dépôt réel éventuel, garantie/plafond pour un partenaire.
2. Créer le compte de connexion de la personne (`/auth/utilisateurs/`) puis rattacher `owner` = cet utilisateur sur la fiche partie prenante, pour que « Mon espace » lui affiche la bonne situation.

**Scénario C — clôture mensuelle**
1. Vérifier les comptages de toutes les caisses/comptes et les contrôles au vert.
2. Clôturer (`/finance/periodes/nouvelle/` puis détail de la période) : le résultat est figé (`ProfitPeriod`), jamais recalculé après coup.
3. Approuver la proposition de distribution préparée par la chargée des finances (`/finance/distributions/<id>/approbation/`).
4. Décider des demandes en attente (`/finance/demandes/<id>/`) : valider (avec exécution immédiate) ou refuser (commentaire obligatoire).

**Limites de l'administrateur**
- Ne peut pas contourner l'idempotence du grand livre : une même clé ne comptabilise jamais deux fois.
- Ne peut pas modifier une période déjà clôturée ni une distribution déjà approuvée.
- Ne peut pas confirmer une remise de caisse qu'il a lui-même initiée (double contrôle : demandeur ≠ confirmateur).
- Toute action est tracée dans la piste d'audit (aucune suppression silencieuse).

## 3. Chargée/chargé des finances

**Son espace** : vues Finance en lecture/préparation (`/finance/`), caisses en **lecture seule**, rapports d'exploitation (`/rapports/centre/`).

**Scénario — préparation de la clôture mensuelle**
1. Consulter les comptages et soldes de toutes les caisses (lecture seule, sans pouvoir créer de remise ni clôturer une caisse à la place d'un agent).
2. Préparer la période financière et la proposition de distribution (`/finance/distributions/<id>/proposition/`) selon la politique en vigueur.
3. Transmettre à l'administrateur pour approbation finale — la chargée des finances ne peut pas s'auto-approuver.
4. Télécharger le rapport hebdomadaire global et les rapports d'exploitation (activité, trésorerie, commissions, charges, résultat, contrôle).

**Limites**
- Pas d'accès à `/auth/utilisateurs/` (aucune gestion de comptes).
- Ne peut pas approuver une distribution ni valider définitivement un transfert (capacité `approve` réservée à l'admin).
- Ne peut pas créer de remise de caisse ni clôturer une caisse (elle consulte, elle ne manipule pas physiquement les fonds).
- Aucun accès aux dossiers personnels des parties prenantes (« Mon espace » des autres).

## 4. Agent

**Son espace** : ses opérations (`/operations/`), sa caisse uniquement (`/caisses/`), son rapport quotidien (`/rapports/centre/`).

**Scénario A — encaisser une opération client**
1. `/operations/nouvelle/` : choisir le service actif, saisir client (nom + identifiant), montant.
2. Prévisualiser (`preview`) le tarif et la commission calculés automatiquement (barème actif, pas de saisie manuelle du tarif).
3. Valider : l'opération est créée avec une clé anti-doublon ; encaisser le paiement (`/operations/<reference>/payer/`).
4. En cas d'erreur : corriger (`revise`) tant que l'opération le permet, ou annuler avec motif obligatoire (`annuler`).

**Scénario B — transférer des fonds à un autre agent (transfert interne)**
1. Finance › Transferts (`/finance/transferts/nouveau/`) : initier depuis sa propre caisse — les fonds passent en transit, l'admin est notifié.
2. L'agent destinataire confirme la réception physique.
3. Les fonds ne deviennent disponibles qu'après validation finale par l'administrateur.

**Scénario C — remise de fin de journée**
1. `/caisses/<id>/remise/` : demander une remise (montant ≤ solde de sa caisse).
2. **Un administrateur** (jamais l'agent lui-même, jamais la personne qui a demandé la remise) confirme la remise (`/caisses/remise/<id>/confirmer/`).
3. Clôture de caisse (`/caisses/<id>/cloture/`) : compter la caisse, prévisualiser l'écart (`cloture/apercu/`) avant de valider — une journée déjà clôturée ne se rouvre pas.

**Limites de l'agent**
- Voit et modifie **uniquement sa propre caisse** et ses propres opérations ; aucune autre caisse d'agent n'est visible.
- Ne peut pas confirmer sa propre remise ni valider un transfert de manière définitive (ces étapes appartiennent à l'admin).
- Ne peut pas enregistrer de charge/dépense : la saisie des charges est réservée à l'administrateur (`apps/expenses`).
- Ne télécharge que son rapport quotidien (journée en cours ou passée), jamais un rapport global ou d'un autre agent.
- Ne peut pas créer ni gérer de comptes utilisateurs, de parties prenantes ou de politiques financières.

## 5. Partenaire

**Son espace** : `/finance/mon-espace/` (situation, garantie, commissions, demandes) et `/finance/mon-espace/commissions/` (choix de destination des commissions).

**Scénario A — consulter sa situation**
- « Mon espace » affiche : garantie active, plafond par opération (maximum 2 000 USD), commissions dues ou déjà converties, historique de ses demandes.

**Scénario B — choisir la destination de ses commissions**
1. `/finance/mon-espace/commissions/` : choisir entre
   - **Conversion automatique en garantie** : chaque commission part est convertie en garantie dès l'opération, sans démarche répétée ;
   - **Conservation dans « Commissions à recevoir »** : les commissions s'accumulent jusqu'à une demande manuelle de conversion.
2. Le choix peut inclure une date d'effet future ; une fois pris, il s'applique aux commissions à venir et reste dans l'historique (jamais supprimé).

**Scénario C — demander une augmentation de garantie ou convertir des commissions**
1. Dans « Mon espace », soumettre une demande : type « Augmenter la garantie » ou « Convertir des commissions en garantie », montant, devise.
2. Suivre le cycle : **Soumise → Validée ou Refusée (avec commentaire de l'admin) → Exécutée**. Rien n'est comptabilisé avant l'exécution.

**Limites du partenaire**
- Ne voit que sa propre fiche partenaire ; aucune vue sur les autres partenaires, agents ou l'activité globale.
- Le plafond par opération ne peut jamais dépasser 2 000 USD et n'est jamais déduit automatiquement du dépôt.
- Ne peut pas soumettre d'autres types de demande (pas de réinvestissement de bénéfice, réservé aux actionnaires ; pas de retrait d'investissement, réservé aux investisseurs).
- Ne peut pas s'auto-valider : toute demande attend une décision de l'administrateur.

## 6. Investisseur

**Son espace** : `/finance/mon-espace/` (investissement, rémunération mensuelle fixe, historique, demandes).

**Scénario A — consulter sa rémunération**
- « Mon espace » affiche le montant fixe mensuel défini par l'admin (jamais un pourcentage du capital ou du résultat) et l'historique des paiements `INVESTOR_RETURN`.

**Scénario B — augmenter ou retirer son investissement**
1. Soumettre une demande « Augmenter l'investissement » ou « Retirer l'investissement » avec montant et devise. Un retrait ne peut pas dépasser l'investissement actuellement en place.
2. Cycle identique : Soumise → Validée/Refusée → Exécutée par l'administrateur.

**Limites de l'investisseur**
- Ne voit que sa propre situation ; aucune vue sur les autres investisseurs ni sur l'activité globale de l'entreprise.
- La rémunération mensuelle ne change pas automatiquement si l'investissement augmente ou diminue : seule une nouvelle règle datée créée par l'admin la modifie.
- En cas de mois déficitaire ou incomplet sans règle contractuelle explicite définie par l'admin, le paiement peut être bloqué ou suspendu — l'investisseur ne peut pas forcer un versement.

## 7. Actionnaire

**Son espace** : `/finance/mon-espace/` (part de capital, bénéfices distribuables, demandes) + consultation globale en lecture seule des opérations.

**Scénario A — consulter l'activité de l'entreprise**
- Accès en lecture seule à l'activité globale et aux opérations en temps réel (analytics), sans pouvoir rien modifier ni créer.

**Scénario B — réinvestir ou retirer un bénéfice**
1. Après une clôture mensuelle et une distribution approuvée, une ligne « bénéfice dû » apparaît dans « Mon espace ».
2. Soumettre une demande « Réinvestir un bénéfice » ou « Retirer un bénéfice », en sélectionnant la distribution concernée, pour un montant total ou partiel, jamais au-delà du disponible restant sur cette distribution.
3. Cycle : Soumise → Validée/Refusée (commentaire) → Exécutée.

**Limites de l'actionnaire**
- La consultation globale est strictement en lecture : aucune action sur les caisses, opérations ou comptes d'autrui.
- Ne peut demander un retrait/réinvestissement que sur une distribution déjà approuvée et non entièrement consommée par d'autres demandes en cours.
- Ne peut pas voir les données personnelles (situation financière) des autres actionnaires, investisseurs ou partenaires.

## 8. Récapitulatif des droits et limites par rôle

| Rôle | Comptes utilisateurs | Grand livre / clôtures | Sa caisse / ses opérations | Sa situation (partie prenante) | Vue globale lecture seule |
|---|---|---|---|---|---|
| Admin | Créer/gérer | Approuver, clôturer, confirmer les remises | Toutes (supervision) | — | Oui |
| Chargée des finances | — | Préparer (pas d'approbation) | Lecture seule | — | Oui (rapports d'exploitation) |
| Agent | — | — | Oui (créer, remettre, clôturer la sienne) | — | — |
| Partenaire | — | — | — | Oui + demandes limitées (garantie, commissions) | — |
| Investisseur | — | — | — | Oui + demandes limitées (investissement) | — |
| Actionnaire | — | — | — | Oui + demandes limitées (bénéfices) | Oui (opérations globales) |

Pour toute question sur les règles comptables sous-jacentes à ces scénarios (apports, transferts, distributions, rémunérations…), voir `finance-workflows.md`.
