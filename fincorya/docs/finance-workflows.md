# FINCORYA — Guide court des nouveaux workflows financiers

Tous les montants passent par le grand livre (`apps/finance`). Aucun solde n'est modifié directement ; chaque action ci-dessous produit exactement un lot d'écritures, rejouable sans doublon grâce à sa clé.

## 1. Apports (Finance › Apports)
Qui : admin (capacité `approve`).
1. « Enregistrer un apport » : origine (actionnaire / investissement / garantie partenaire), partie, montant, devise, date, compte de réception.
2. Écriture : trésorerie DÉBIT ↔ `CAPITAL-<devise>-<partie>` / `INVESTOR_FUNDS-…` / `PARTNER_GUARANTEE-…` CRÉDIT.
3. Une reprise de soldes (`OPENING-EQUITY`) n'est jamais un apport : ne ressaisissez pas les soldes migrés.

## 2. Transferts internes (Finance › Transferts)
Qui : finance (`prepare`) pour initier, admin (`approve`) pour confirmer / annuler.
1. Initiation : la source est créditée immédiatement, le montant passe en `TRANSIT`, les frais éventuels deviennent une charge `EXPENSE-<devise>-TRANSFER_FEE`.
2. Confirmation de réception : la destination est débitée, le transit se vide.
3. Annulation (motif obligatoire) : les fonds reviennent à la source ; les frais restent une charge tracée.
Le total de trésorerie + transit est constant hors frais.

## 3. Partenaires (Finance › Garanties)
1. Définir la garantie : devise et **plafond par opération**. Sans garantie active, aucune opération ne peut être attribuée au partenaire.
2. À chaque opération attribuée, le système vérifie : montant ≤ plafond **et** montant ≤ garantie détenue.
3. Commission : `fee × part partenaire (règle datée)` → `PARTNER_PAYABLE` ; le reste → produits FINCORYA. Le **frais fournisseur** (`Operation.supplier_fee`) est une charge FINCORYA séparée et ne modifie jamais le partage.
4. Conversion en garantie : via une demande `CONVERT_COMMISSION` (voir §5) ; aucun encaissement, `PARTNER_PAYABLE` → `PARTNER_GUARANTEE`.

## 4. Clôture mensuelle et distribution (Finance › Clôtures, Bénéfices, Distribution)
Prérequis : une **politique de distribution datée** créée par l'admin en choisissant les actionnaires existants, le mode et la date d'effet.
Modes : `EQUAL_SHARES` (parts égales — sans sélection, tous les actionnaires actifs ; confirmé : 25 % chacun des quatre actionnaires), `CAPITAL_PROPORTIONAL` (proportionnel aux apports — disponible mais non activée par défaut), `RULE_PERCENT` (règles datées historiques). La participation au capital reste calculée séparément des apports réels.
1. Comptages de toutes les caisses/comptes, contrôles au vert, puis « Clôturer ».
2. Le résultat est figé dans `ProfitPeriod` : commissions propres + part FINCORYA − salaires − autres charges − frais fournisseurs − rémunérations investisseurs − pertes reportées = distribuable. Les écarts de change sont comptabilisés à part (`FX_DIFFERENCE-…-REALIZED` / `-REVALUATION`), exclus du distribuable et affichés dans le rapport de résultat tant que leur traitement n'est pas décidé.
3. Perte : aucune distribution possible ; le montant est reporté sur le mois suivant.
4. Proposition (finance) puis approbation (admin) : les parts sont réparties au centime selon le plan figé à la clôture ; la dernière part absorbe l'arrondi. Les périodes clôturées ne sont jamais recalculées.
5. Règlement ou réinvestissement : par l'admin, ou via les demandes des actionnaires.

## 4bis. Rémunération mensuelle des investisseurs
Montant **fixe** saisi par l'admin par règle datée `REMUNERATION` (Finance › Règles) — jamais un pourcentage du capital ni du résultat. La hausse ou le retrait d'un investissement ne modifie pas ce montant ; chaque changement est une nouvelle version datée.
Conditions contractuelles obligatoires (Finance › Rémunérations) : dates de début/fin de contrat, règle de **mois incomplet** (prorata / mois complet / rien) et règle de **mois déficitaire** (due / suspendue). Sans ces règles explicites, la clôture du mois concerné est bloquée — aucun défaut n'est inventé.
Génération : une charge `INVESTOR_RETURN` par investisseur et par mois, créée à la clôture avec une clé d'acquisition unique (pas de doublon). Le paiement n'impute pas la charge une seconde fois.

## 4ter. Conversions de devises (Finance › Conversions)
Saisie par l'admin (`approve`) : compte source, compte destination (autre devise), montant converti, montant reçu, frais. Le taux appliqué est conservé ; l'écart réalisé par rapport au taux de référence publié est isolé en `FX_DIFFERENCE-…-REALIZED`, distinct des réévaluations (`-REVALUATION`, saisies par écriture dédiée) et **jamais classé en commission ni distribué**.

## 5. Demandes des parties prenantes (Mon espace › Nouvelle demande / Finance › Demandes)
Actionnaire : réinvestir / retirer un bénéfice (total ou partiel, jamais au-delà du disponible).
Investisseur : augmenter / retirer l'investissement.
Partenaire : augmenter la garantie / convertir des commissions dues en garantie.
Cycle : **Soumise → Validée ou Refusée (commentaire obligatoire) → Exécutée**. Rien n'est comptabilisé avant l'exécution ; l'exécution est idempotente.

## 6. Centre de rapports (`/rapports/centre/`)
Rapports : activité, fonds apportés, trésorerie, commissions, charges, investisseurs, partenaires, actionnaires, résultat mensuel, contrôle.
Périodes explicites : journée, semaine (convention affichée, `FINANCE_WEEK_START`), mois, trimestre, année, personnalisée.
Sorties : écran (HTMX), PDF, Excel, CSV — mêmes agrégats (`apps/finance/reporting.py`). En-tête : période + fuseau, filtres, devise, auteur, statut PROVISOIRE / CLÔTURÉ, identifiant + version.
Périmètre appliqué côté serveur :
- Agent : rapport quotidien de ses seules opérations et de sa caisse, journée passée au choix.
- Chargée des finances : rapports globaux d'exploitation (activité, trésorerie, commissions, charges, résultat, contrôle) ; pas de dossiers personnels.
- Actionnaire : sa situation + activité globale en lecture.
- Investisseur / partenaire : leur situation uniquement.
- Téléchargements : rôles listés dans `REPORT_DOWNLOAD_ROLES` (défaut : admin, finance, agent).

## 7. Création manuelle des parties, dépôts, garanties et caisses
Aucun compte partenaire, investisseur, actionnaire, garantie, dépôt, plafond ou écriture de démonstration n'est créé automatiquement. L'admin doit créer chaque partie explicitement (Finance › Parties prenantes › Créer une partie), avec éventuellement : dépôt réel, devise, date de réception, compte récepteur, plafond (partenaire), justificatif. Un montant coché « déjà compris dans la reprise » est reclassé via les soldes d'ouverture et ne génère pas d'encaissement. Les caisses agent post-bascule (Finance › Comptes financiers › Ouvrir une caisse agent) sont créées vides ; leur allocation de fonds est un mouvement tracé distinct.

## 8. Paramètres à renseigner par l'admin
1. Politique de distribution `EQUAL_SHARES` : sélectionner les actionnaires existants, mode, date d'entrée en vigueur.
2. Rattachement `Stakeholder.owner` → utilisateur pour chaque actionnaire / investisseur / partenaire.
3. Garantie et plafond de chaque partenaire (max 2 000 USD, saisi manuellement, **jamais déduit du dépôt**).
4. Rémunération fixe mensuelle de chaque investisseur (`EconomicRule` `REMUNERATION`) + conditions contractuelles (`RemunerationTerms`).
5. `FINANCE_WEEK_START`, `REPORT_DOWNLOAD_ROLES`.
6. Reprise manuelle : origine des 1 540 USD de cash en main, position Fantiny −369 USD (les éléments contextuels du brief ne donnent lieu à aucune création automatique).

## 9. Ordre de configuration initiale (administrateur)
1. **Utilisateurs et rôles** : créer les comptes admin, chargée des finances, agents ; activer MFA et désactiver `LOCAL_AUTH_BYPASS` en production.
2. **Devises et taux** : vérifier les devises actives, publier les taux `ExchangeRate` (référence USD) et les grilles tarifaires.
3. **Caisse globale** : créer/ajuster `GlobalCashAccount` par devise avec un encaissement justifié.
4. **Bascule du grand livre** : préparer puis appliquer `migrate_opening_balances` avec son empreinte ; résoudre les anomalies de reprise.
5. **Comptes de service** : créer les comptes M-Pesa/Digital/etc. nécessaires (Finance › Comptes financiers).
6. **Parties prenantes** : créer chaque actionnaire, investisseur, partenaire via « Créer une partie » ; saisir dépôts réels et garanties (max 2 000 USD/opération, jamais déduits du dépôt) ; rattacher `owner` au compte utilisateur.
7. **Rémunérations** : règle datée `REMUNERATION` (montant fixe + devise) et `RemunerationTerms` (dates de contrat, mois incomplet, mois déficitaire) pour chaque investisseur concerné.
8. **Distribution** : politique datée `EQUAL_SHARES` (ou `CAPITAL_PROPORTIONAL` si décidé) avec actionnaires et pourcentages totalisant 100 %.
9. **Caisses agent** : ouvrir les caisses post-bascule puis allouer les fonds depuis la caisse globale.
10. **Clôtures et rapports** : comptages périodiques, clôture mensuelle, vérification des exports ; `FINANCE_WEEK_START` et `REPORT_DOWNLOAD_ROLES` selon la gouvernance.
Les données du brief (dont Fantiny) restent de l'évidence à documenter manuellement : aucun montant, compte ni écriture n'est prérempli.
