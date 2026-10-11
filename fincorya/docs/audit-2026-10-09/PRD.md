# FINCORYA — Product Requirements Document

**Version 1.0 · 9 octobre 2026 · Référence produit issue de l'audit**

## 1. Vision et problème résolu

FINCORYA centralise les opérations financières quotidiennes et permet de comprendre, pour chaque montant, **son origine, sa destination, son propriétaire économique et son statut de validation**. L'application relie le travail des agents, les contrôles de la fonction finance et les décisions de l'administrateur aux situations des partenaires, investisseurs et actionnaires.

Les risques produit à maîtriser sont le double encaissement d'un apport, le double paiement, l'écart entre cash et résultat, la distribution sans politique explicite et la perte de contexte lors des corrections. Les rapports doivent permettre d'expliquer un chiffre plutôt que seulement de l'afficher.

**Décision confirmée : connexion sans MFA.** Le propriétaire a volontairement désactivé le MFA. Les exigences de session, mot de passe, permissions et traçabilité restent en vigueur. Les traces techniques MFA existantes ne constituent pas une exigence de réactivation.

## 2. Objectifs et indicateurs

| Objectif | Indicateur proposé | Cible de qualification | État de mesure |
|---|---|---|---|
| Empêcher les écritures en double | Doubles paiements/encaissements dans les scénarios de retry | 0 | Tests existants, concurrence PostgreSQL à exécuter |
| Expliquer les soldes | Différence projection/grand livre par compte | 0 avant clôture | Pas de contrôle des soldes réels dans l'audit |
| Respecter le périmètre utilisateur | Accès croisés indus dans la matrice de recette | 0 | Plusieurs tests disponibles, revue exhaustive à maintenir |
| Fermer correctement les périodes | Clôtures sans anomalie non résolue | 100 % des clôtures acceptées | Contrôles codés |
| Rendre les rapports cohérents | Écarts inexpliqués entre formats à périmètre égal | 0 | Sources multiples à rapprocher |
| Assurer un parcours complet | Scénarios P1 réussis | 100 % avant livraison qualifiée | Non atteint : AUD-01 à 07 |

Ces cibles sont proposées pour la recette ; aucun taux d'usage, chiffre d'affaires ou volume de transactions réel n'a été mesuré. Le temps moyen de saisie, le délai de validation des charges et le délai de clôture sont des indicateurs à instrumenter, sans inventer de baseline.

## 3. Utilisateurs et besoins

| Persona | Besoin principal | Responsabilité / restriction |
|---|---|---|
| Administrateur | Gouverner comptes, règles, validations et règlements | Validation finale des actes financiers sensibles |
| Chargé des finances | Préparer les écritures, contrôler les positions, proposer une distribution | Préparation ≠ approbation ; pas de validation finale implicite |
| Agent | Saisir les opérations, gérer sa caisse et ses justificatifs | Périmètre de ses caisses et opérations ; pas celles d'autres agents |
| Partenaire | Comprendre commissions et garantie, exprimer son choix de conversion | Partie liée au compte utilisateur uniquement |
| Investisseur | Suivre apport, rémunération et demandes | Situation propre, rémunération fixe selon règle datée |
| Actionnaire | Suivre l'activité globale et ses droits personnels | Lecture globale autorisée, écritures seulement via demandes personnelles prévues |

Un rôle utilisateur est distinct d'une partie économique. Créer un utilisateur partenaire ne signifie pas enregistrer un dépôt. La relation explicite entre les deux détermine l'accès à « Mon espace ».

## 4. Périmètre

### Inclus dans le produit observé

Comptes utilisateurs, opérations de transfert/retrait, caisses multidevises, charges, services de transaction configurables, grand livre, apports, garanties, transferts internes, règles datées, rémunérations, clôtures, distributions, demandes, notifications et rapports privés. Les modèles de contrats/version de documents existent, mais leur parcours utilisateur autonome n'est pas démontré.

### Non établi ou hors de cette version

Applications mobiles natives, mode hors ligne, plateforme multi-entreprises isolées, portail client grand public, initiation automatique de transactions auprès de M-Pesa/PayPal/autres opérateurs, synchronisation bancaire automatique, API publique, signature électronique intégrée et certification réglementaire. Les noms de services dans un formulaire ne prouvent pas une intégration à leurs API.

La mission présente fournit les spécifications et l'audit ; elle ne réalise ni réactivation MFA, ni bascule comptable, ni traitement de données réelles.

## 5. Exigences fonctionnelles

| ID / priorité | Exigence | État observé | Critère d'acceptation |
|---|---|---|---|
| **PRD-01 / P1** | Accéder par e-mail et mot de passe, sans MFA | Connexion directe implémentée ; config/formulaires incohérents | Compte actif valide → dashboard sans OTP ; anonyme → login ; mauvais mot de passe → refus ; config production sans MFA acceptée |
| **PRD-02 / P1** | Gérer utilisateurs et accès | Admin, invitations, activation et désactivation présents | Adresse unique ; invitation expirée ou remplacée inutilisable ; compte inactif sans accès ; aucun dépôt créé par création de compte |
| **PRD-03 / P1** | Enregistrer une opération explicite et traçable | Implémenté | Montant positif, devise de caisse, frais explicites même nuls ; solde/impact visibles ; reçu en attente sans décaissement ; paiement unique |
| **PRD-04 / P1** | Maîtriser la caisse et les écarts | Implémenté avec revue d'écart récente | Écart justifié → REVIEW et caisse bloquée ; aucune correction automatique ; admin seul valide avec motif et audit |
| **PRD-05 / P1** | Distinguer fonds reçus, repris et alloués | Implémenté sous grand livre actif | Apport neuf reçu une seule fois ; montant repris sans nouvelle entrée de cash ; allocation conserve le total hors frais ; reprise du même client_key sans duplication |
| **PRD-06 / P1** | Suivre les transferts internes jusqu'à validation | Implémenté sous grand livre actif | INITIATED → RECEIVED → CONFIRMED ; cash indisponible avant validation finale ; annulation seulement avec retour documenté |
| **PRD-07 / P1** | Encadrer les opérations partenaires | Implémenté | Garantie active positive ; plafond explicite ; taux daté figé ; frais fournisseur séparé ; choix de conversion historisé |
| **PRD-08 / P1** | Reconnaître les charges puis les payer | Implémenté | Admin décide ; approbation seule ne signifie pas paiement ; cumul des règlements borné par la dette ; justification consultable par rôle |
| **PRD-09 / P1** | Clôturer et distribuer sans recalcul historique | Implémenté dans Finance, transition à qualifier | Anomalie bloque clôture ; mois fini et comptages cohérents ; snapshot figé ; pertes déduites ; montant proposé ≤ disponible ; approbation admin |
| **PRD-10 / P1** | Donner aux parties un canal de demandes | Implémenté | Situation propre ; demande soumise sans cash ; décision tracée ; exécution unique avec contrôles de solde et de droits |
| **PRD-11 / P1** | Produire des rapports explicables et autorisés | Implémenté, sources et état vide à harmoniser | Période/devise/source/statut visibles ; périmètre serveur ; mêmes totaux entre formats équivalents ; déclaration absente sans lien cassé |
| **PRD-12 / P2** | Notifier les événements utiles | Implémenté, exploitation à qualifier | Notification persistée ; état d'envoi vérifiable ; retry sans créer un nouvel événement métier ; échec SMTP sans annulation de clôture |
| **PRD-13 / P2** | Gérer contrats et versions dans un parcours utilisable | Modèles présents ; parcours complet non établi | Cible : accès autorisé aux versions, dates et statuts, rattachement à la partie, fichiers privés |
| **PRD-14 / P2** | Maintenir une interface cohérente et accessible | Socle existant | Menu et formulaires utilisables au clavier/mobile ; statuts textuels ; chiffres/devise lisibles ; erreurs et états vides explicites |

## 6. Règles métier de référence

Ces règles décrivent **le code actuel**. Hormis la décision sans MFA, leur présence dans ce PRD ne remplace pas une approbation métier explicite.

| ID | Règle observée |
|---|---|
| RM-01 | Les montants et soldes restent séparés par devise ; une conversion explicite conserve montants, taux et frais. |
| RM-02 | Une commission partenaire est partagée avec le taux figé sur l'opération. Le défaut actuel du partenaire est 60 %, soit 40 % FINCORYA, sauf règle datée applicable. |
| RM-03 | Le frais fournisseur est une charge distincte ; il ne modifie pas le pourcentage de partage des commissions. |
| RM-04 | La garantie partenaire doit être active et positive ; le plafond par opération est configuré explicitement et ne dépasse pas 2 000 USD équivalents. L'encours n'est pas simplement plafonné au dépôt. |
| RM-05 | Les nouvelles politiques de distribution sélectionnent exactement quatre actionnaires à 25 % chacun. Les anciens modes persistent pour l'historique. |
| RM-06 | Le résultat distribuable tient compte des charges et des pertes reportées. Les différences de change sont isolées et exclues du distribuable actuel. |
| RM-07 | Une rémunération investisseur repose sur un montant fixe daté, pas sur un pourcentage du capital. Les mois incomplets et déficitaires nécessitent les conditions applicables ; ne pas inventer un défaut de paiement. |
| RM-08 | L'ajout d'un bénéficiaire ou la modification d'une règle ne réécrit pas une clôture précédente. |
| RM-09 | Les écritures publiées se corrigent par contre-passation ; les reçus et déclarations historiques ne doivent pas être comptés deux fois. |
| RM-10 | Les périodes utilisent le fuseau métier configuré, par défaut Europe/Istanbul ; le fuseau de l'utilisateur de cet audit est Europe/Bucharest et ne remplace pas cette convention. |

Exemple pédagogique, **sans valeur de solde réel** : une commission de 100 USD avec part partenaire figée à 60 % donne 60 USD au partenaire et 40 USD à FINCORYA ; un frais fournisseur de 3 USD reste une charge séparée. Un apport de 1 000 USD n'est pas un revenu de commission et ne devient pas directement un bénéfice distribuable.

## 7. Exigences transversales

- **Intégrité :** unicité du résultat après retry, équilibre des écritures, absence de paiement au-delà du disponible, traçabilité des corrections.
- **Confidentialité :** contrôles serveur par rôle et propriétaire, accès privé aux justificatifs/exports ; masquer un bouton ne suffit pas.
- **Compréhension :** distinguer théorique/compté, reçu/transit/disponible, approuvé/payé, déclaré/enregistré et provisoire/clôturé.
- **Accessibilité :** navigation clavier, labels, focus visible, texte des statuts, tableaux défilants sur mobile ; cible interne détaillée dans le design system.
- **Fiabilité :** défaillance de notification séparée de la réussite d'un acte financier ; sauvegarde et restauration vérifiables.
- **Performance :** budget de réponse à définir et mesurer sur une volumétrie représentative. Aucun engagement de capacité n'est déduit du nombre de workers.

## 8. Recette prioritaire

| Cas | Étant donné | Quand | Alors |
|---|---|---|---|
| AC-01 | MFA désactivé, utilisateur actif | Connexion correcte puis changement du mot de passe | Aucun code MFA demandé ; session et ancien mot de passe contrôlés |
| AC-02 | Agent A connecté | URL/référence appartenant à B | Aucun détail de B accessible |
| AC-03 | Reçu en attente et fonds suffisants | Deux demandes de paiement simultanées | Une seule sortie et un seul paiement effectif |
| AC-04 | Comptage différent du théorique | Agent clôture avec justification | REVIEW ; solde inchangé ; caisse bloquée |
| AC-05 | Écart en REVIEW | Admin corrige avec justification, solde stable | Un mouvement correctif ; clôture fermée ; écart original conservé |
| AC-06 | Transfert interne initié | Destinataire confirme la réception | Fonds encore en transit jusqu'à validation admin |
| AC-07 | Apport déjà reçu | Même demande rejouée ou reçu historique rapproché | Pas de nouvel encaissement |
| AC-08 | Commission ancienne à 60 %, règle nouvelle à 70 % | Rapport d'une période incluant les deux | Chaque opération conserve son taux et son arrondi |
| AC-09 | Mois clôturé | Modification d'une règle puis consultation | Résultat clôturé inchangé |
| AC-10 | Base sans déclaration de septembre | Visite du centre de rapports | État vide explicite, aucun chiffre inventé |
| AC-11 | Même périmètre de rapport | Écran, CSV, XLSX et PDF | Totaux comparables identiques et devise visible |
| AC-12 | Deux paiements d'une charge en concurrence | Validation simultanée | Dette non surpayée ; preuve sous PostgreSQL |

## 9. Priorités de livraison

| Étape | Livrable attendu | Condition de sortie |
|---|---|---|
| Stabilisation accès | PRD-01/02, AUD-01/02/03 | Tests d'authentification alignés, profil utilisable |
| Stabilisation finance | PRD-03 à 10 | Moteur nominal défini, reprise répétable et suite PostgreSQL réussie |
| Stabilisation rapports | PRD-11 | État vide, sources explicites, cohérence interformats |
| Qualité d'usage | PRD-12/14 | Notifications observables, recette mobile/clavier |
| Évolution documentaire | PRD-13 | Besoin contrats approuvé avant création d'un parcours supplémentaire |

Aucun délai calendaire n'est promis sans estimation de l'équipe et arbitrage des règles. Les propositions de ce tableau ne sont pas des changements déjà effectués.

## 10. Décisions encore ouvertes

| ID | Décision à prendre | Comportement actuel / proposition |
|---|---|---|
| DEC-01 | Suppression complète des résidus MFA, y compris mot de passe ? | Accès sans MFA confirmé ; cible proposée : mot de passe sans second facteur obligatoire également |
| DEC-02 | Confirmer les quatre bénéficiaires et la règle 25 % | Code l'impose aux nouvelles politiques ; identités non reproduites dans ce dossier |
| DEC-03 | Confirmer clôture quotidienne par l'agent | Code l'autorise sur sa caisse ; admin corrige les écarts |
| DEC-04 | Confirmer 60/40, plafond partenaire et traitement FX | Règles observées documentées ci-dessus ; conserver l'historique |
| DEC-05 | Choisir le mode financier nominal et la date de bascule | Ne pas activer le flag sans reprise réconciliée |
| DEC-06 | Confirmer fuseau métier et début de semaine | Europe/Istanbul, lundi par défaut dans le dépôt |
| DEC-07 | Généraliser la déclaration mensuelle à d'autres périodes | Proposition ; URL actuelle spécifique à septembre 2026 |
| DEC-08 | Définir restauration, rétention et objectifs de service | Mesures et essai opérationnel requis |

Les décisions ouvertes n'empêchent pas d'utiliser ce dossier pour comprendre l'existant ; elles empêchent seulement d'affirmer que certaines évolutions sont approuvées. Voir [TRD](TRD.md) pour leur traduction technique et [App Flow](APP-FLOW.md) pour les parcours.
