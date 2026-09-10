# FINCORYA — Guide court des nouveaux workflows financiers

Tous les montants passent par le grand livre (`apps/finance`). Aucun solde n'est modifié directement ; chaque action ci-dessous produit exactement un lot d'écritures, rejouable sans doublon grâce à sa clé.

## 1. Apports (Finance › Apports)
Qui : admin (capacité `approve`).
1. « Enregistrer un apport » : origine (actionnaire / investissement / garantie partenaire), partie, montant, devise, date, compte de réception.
2. Écriture : trésorerie DÉBIT ↔ `CAPITAL-<devise>-<partie>` / `INVESTOR_FUNDS-…` / `PARTNER_GUARANTEE-…` CRÉDIT.
3. Une reprise de soldes (`OPENING-EQUITY`) n'est jamais un apport : ne ressaisissez pas les soldes migrés.

## 2. Transferts internes (Finance › Transferts)
Qui peut initier : un agent depuis sa propre caisse, la chargée des finances ou l'admin.
Parcours complet :
1. **Initiation** : l'expéditeur débite sa caisse, le montant passe en transit, l'admin est notifié immédiatement. Les frais éventuels deviennent une charge `EXPENSE-<devise>-TRANSFER_FEE`.
2. **Réception** : l'agent destinataire confirme la réception physique. Les fonds restent en transit et ne sont pas encore disponibles.
3. **Validation finale** : l'admin valide définitivement. La destination est débitée, le transit se vide, les fonds deviennent disponibles chez le destinataire.
4. **Annulation** : un motif est obligatoire. Les fonds ne sont **pas automatiquement recrédités** à l'expéditeur ; un retour effectif doit être confirmé avec justificatif, puis l'admin valide l'annulation.
Le total de trésorerie + transit est constant hors frais.

## 3. Partenaires (Finance › Garanties)
1. Définir la garantie : devise et **plafond par opération**. Sans garantie active, aucune opération ne peut être attribuée au partenaire.
2. À chaque opération attribuée, le système vérifie : montant ≤ plafond par opération (max 2 000 USD) **et** garantie active à solde positif. L'opération peut dépasser le montant de la garantie tant qu'elle reste dans le plafond.
3. Commission : `fee × part partenaire (règle datée)` → `PARTNER_PAYABLE` ; le reste → produits FINCORYA. Le **frais fournisseur** (`Operation.supplier_fee`) est une charge FINCORYA séparée et ne modifie jamais le partage.
4. Conversion en garantie : via une demande `CONVERT_COMMISSION` (voir §5) ; aucun encaissement, `PARTNER_PAYABLE` → `PARTNER_GUARANTEE`.

## 3bis. Choix du partenaire pour ses commissions (Mon espace › Mes commissions)
Le partenaire connecté choisit entre deux options, historisées avec date d'effet :
- **Option A — Conversion automatique en garantie** : chaque commission partenaire est automatiquement convertie en garantie dès l'opération, sans demande manuelle répétée. La conversion est tracée sans nouvel encaissement et sans doublon.
- **Option B — Conservation dans « Commissions à recevoir »** : les commissions s'accumulent dans `PARTNER_PAYABLE` jusqu'à une demande manuelle de conversion.
Le choix est historisé (`PartnerCommissionChoice` avec `effective_at`) et ne peut pas être supprimé. Les commissions accumulées avant le choix restent convertibles via une demande `CONVERT_COMMISSION`.

## 4. Clôture mensuelle et distribution (Finance › Clôtures, Bénéfices, Distribution)
Prérequis : une **politique de distribution datée** créée par l'admin en choisissant les actionnaires existants, le mode et la date d'effet.
Modes : `EQUAL_SHARES` (parts égales entre les actionnaires **sélectionnés** — la sélection est obligatoire, pas de fallback automatique ; politique initiale : quatre actionnaires à 25 % chacun), `CAPITAL_PROPORTIONAL` (proportionnel aux apports — disponible mais non activé par défaut), `RULE_PERCENT` (règles datées historiques). L'ajout d'un nouvel actionnaire ne modifie pas silencieusement une politique existante ni une distribution clôturée : il faut créer une nouvelle politique datée.
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
- **Agent** : téléchargement de son rapport quotidien uniquement (journée passée ou en cours, ses opérations et sa caisse).
- **Chargée des finances** : téléchargement du rapport hebdomadaire global et préparation de la clôture mensuelle (rapports d'exploitation : activité, trésorerie, commissions, charges, résultat, contrôle) ; pas de dossiers personnels.
- **Admin** : tous les rapports et toutes les périodes.
- **Actionnaire** : consultation de toutes les opérations en temps réel et analytics (activité globale en lecture), plus ses demandes personnelles autorisées.
- **Investisseur / partenaire** : leur situation uniquement.
- Téléchargements : rôles listés dans `REPORT_DOWNLOAD_ROLES` (défaut : admin, finance, agent).

## 7. Création manuelle des parties, dépôts, garanties et caisses
Aucun compte partenaire, investisseur, actionnaire, garantie, dépôt, plafond ou écriture de démonstration n'est créé automatiquement. L'admin doit créer chaque partie explicitement (Finance › Parties prenantes › Créer une partie), avec éventuellement : dépôt réel, devise, date de réception, compte récepteur, plafond (partenaire), justificatif. Un montant coché « déjà compris dans la reprise » est reclassé via les soldes d'ouverture et ne génère pas d'encaissement. Les caisses agent post-bascule (Finance › Comptes financiers › Ouvrir une caisse agent) sont créées vides ; leur allocation de fonds est un mouvement tracé distinct.

## 8. Paramètres à renseigner par l'admin
1. Politique de distribution `EQUAL_SHARES` : sélectionner les actionnaires existants, mode, date d'entrée en vigueur.
2. Rattachement `Stakeholder.owner` → utilisateur pour chaque actionnaire / investisseur / partenaire.
3. Garantie et plafond de chaque partenaire (max 2 000 USD, saisi manuellement, **jamais déduit du dépôt**).
4. Rémunération fixe mensuelle de chaque investisseur (`EconomicRule` `REMUNERATION`) + conditions contractuelles (`RemunerationTerms`).
5. `FINANCE_WEEK_START`, `REPORT_DOWNLOAD_ROLES`.
6. Reprise manuelle : l'admin crée chaque compte et saisit les montants vérifiés. Aucun montant réel non confirmé n'est prérempli (voir annexe historique).

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
Aucun montant, compte ni écriture n'est prérempli. Les éléments contextuels du brief restent de l'évidence à documenter manuellement par l'admin (voir annexe historique).

## 10. Statut de `confirmed_brief.json`
`confirmed_brief.json` est un paquet d'évidence historique passé en revue. Il peut être versé via `stage_import` pour **préparer des lignes de reprise à examiner** (`ImportRow`), chaque ligne recevant des anomalies (« date d'effet non confirmée », « partie à rattacher », « solde inconnu », etc.).
Il ne constitue **jamais une source de montants validés** : aucune ligne n'est comptabilisée, ni partie, compte, dépôt, garantie ni écriture créée, sans rattachement, vérification et validation explicites de l'administrateur (`review_import` avec résolution documentée).

## 11. Éviter le double comptage des fonds
Trois parcours distincts, à ne pas confondre :
1. **Reprise de soldes existants** (`OPENING-EQUITY`) : aucun nouvel encaissement. Les soldes migrés sont figés à la bascule.
2. **Nouvel apport** (`CONTRIBUTION`) : un seul encaissement, trésorerie DÉBIT ↔ capital/garantie CRÉDIT.
3. **Allocation vers une caisse ou un service** : déplacement de fonds existants (caisse globale → caisse agent ou service), pas de nouvel encaissement.

## 12. Annexe historique (non validée)
Les éléments suivants proviennent du brief contextuel et ne sont **pas validés**. Ils ne doivent pas devenir des étapes obligatoires de configuration. L'admin les documente manuellement après vérification :
- Cash en main mentionné dans le brief : montant à confirmer par l'admin.
- Position Fantiny mentionnée dans le brief : montant à confirmer par l'admin.
- Tout autre montant réel non confirmé : à saisir explicitement par l'admin après rattachement et vérification.

## 13. Annexe technique (modèles et comptes)
- `FinancialAccount` : compte du grand livre (caisse, service, transit, capital, garantie, commissions, charges, etc.).
- `JournalBatch` / `LedgerEntry` : lot d'écritures immuable, identifié par clé d'idempotence.
- `FundContribution` : apport tracé (actionnaire, investisseur, garantie partenaire).
- `InternalTransfer` : transfert interne (initiation, réception, validation finale, annulation).
- `PartnerGuarantee` : garantie partenaire (devise, plafond par opération, active).
- `PartnerCommissionChoice` : choix du partenaire (conversion auto ou conservation).
- `CommissionConversion` : conversion tracée sans doublon.
- `DistributionPolicy` / `DistributionPolicyShare` : politique datée avec actionnaires sélectionnés.
- `StakeholderRequest` : demande d'une partie prenante (réinvestir, convertir, etc.).
- `EconomicRule` : règle datée (commission partenaire, rémunération investisseur).
- `RemunerationTerms` : conditions contractuelles d'un investisseur.
- `FinancialPeriod` / `ProfitPeriod` : période financière et résultat mensuel.
- `CurrencyConversion` : conversion de devises avec écart isolé.
- `ImportRow` : ligne de reprise en attente de revue.
