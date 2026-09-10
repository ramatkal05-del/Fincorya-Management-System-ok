# FINCORYA — Audit ciblé de l'existant et plan de mise à jour financière

Statut : document de travail, mis à jour à chaque lot livré.
Périmètre : `apps/finance`, `apps/cash`, `apps/operations`, `apps/stakeholders`, `apps/profits`, `apps/expenses`, `apps/reports`, `apps/accounts`.

## 1. Ce qui existe déjà et qui est conservé

| Domaine | État vérifié | Décision |
|---|---|---|
| Grand livre (`finance.JournalBatch` / `LedgerEntry`) | Écritures équilibrées par devise, lots immuables une fois publiés, contre-passation obligatoire pour corriger, clé d'idempotence unique, mutex de registre (`LedgerMutex`) | Conservé tel quel ; devient la seule source des soldes |
| Comptes financiers (`FinancialAccount`) | Types caisse agent / globale / mobile money / service / numérique / capital / commissions / charges / dettes ; `cached_balance` recalculable ; `ceiling` | Conservé ; ajout d'un rattachement service ↔ plusieurs comptes déjà possible via `service` (FK vers `ServiceCatalog`) |
| Reprise legacy (`cutover.py`) | Simulation + empreinte, écriture d'ouverture unique par caisse, réconciliation par compte, refus d'une seconde reprise | Conservé ; aucune double reprise possible |
| Clôture (`closing.py`) | Contrôles bloquants (comptages, brouillons, charges en attente, commissions non attribuées), période verrouillée immuable, `ProfitPeriod` figé avec `snapshot` des lots et des règles | Conservé ; le calcul du résultat est étendu (voir §3) |
| Règles datées (`EconomicRule`) | Commission partenaire %, rémunération mensuelle, dividende % ; versions figées, jamais modifiées | Conservé ; les anciennes règles 25 % restent l'historique |
| Caisse legacy (`cash.*`) | Mouvements append-only, verrouillage `select_for_update`, clôture journalière | Conservé comme projection de compatibilité |
| Alertes (`notifications`) | Opération, > 1 000 USD, annulation, dépense, écart de clôture | Conservé ; ajout des alertes demandes / validations |

## 2. Écarts constatés par rapport au brief

### 2.1 Fonds et origine (brief §3, §4)
- Aucun modèle d'**apport** (actionnaire, investisseur, garantie partenaire). Le capital n'existe que via `cash.GlobalCashMovement(CAPITAL_IN)` → écriture `CAPITAL-<devise>` sans porteur économique ni origine.
- Aucune distinction **encaissement** / **allocation** : l'allocation legacy (`allocate_cash`) déplace bien des fonds sans les créer, mais rien n'empêche de saisir un apport deux fois hors reprise.
- Aucune notion de **fonds en transit** confirmés : `record_funding` débite/crédite instantanément.
- `Investment` existe (montant, date) mais sans écriture comptable ni contrat lié aux rémunérations.

### 2.2 Partenaires (brief §6)
- Le partage 60/40 est appliqué sur `operation.fee` (commission brute) — correct.
- Aucun champ **frais fournisseur** : impossible de garantir la règle « le frais fournisseur reste une charge FINCORYA et ne transforme pas 60/40 en 57/38 ».
- Aucune **garantie** partenaire ni **plafond par opération** : `FinancialAccount.ceiling` existe mais n'est vérifié nulle part.
- Aucun mécanisme de conversion « commission due → garantie ».

### 2.3 Résultat et distribution (brief §7, §8)
- `period_report` calcule `produits − charges reconnues`. Les rémunérations investisseurs et les frais fournisseurs ne sont pas isolés ; les pertes antérieures ne sont pas reportées.
- `approve_distributions` exige **quatre règles à 25 %** codées en dur : la nouvelle règle (parts égales entre actionnaires actifs, quel que soit leur nombre) n'est pas paramétrable, et la date d'entrée en vigueur n'est pas tracée.
- `profits.services.calculate_profit_period` (60/20/20) est un moteur historique concurrent ; il n'est plus appelé par la clôture finance mais reste importable.
- Aucun objet **demande** (réinvestissement, retrait, augmentation de garantie) avec validation.

### 2.4 Droits d'accès (brief §11)
- `FinancePolicy` ne couvre que ADMIN / FINANCE_MANAGER / AGENT. Les rôles SHAREHOLDER, INVESTOR, PARTNER n'ont **aucune** politique : ils ne voient rien, mais rien n'est prévu pour leur donner une lecture limitée à leur situation.
- `reports.views.REPORT_ROLES = {ADMIN, AGENT}` ; la chargée des finances n'a pas de rapport hebdomadaire ; l'agent ne peut pas choisir une journée passée dans un rapport quotidien dédié.
- Les exports Excel écrivent les cellules commençant par `=` comme **formules** (vérifié : `openpyxl` type `f`). Les cellules `+`, `-`, `@` sont des chaînes. Le CSV finance échappe déjà ces préfixes ; l'export opérations ne le fait pas.

### 2.5 Rapports (brief §12, §13)
- Deux générateurs : `reports.services` (opérations, mensuel consolidé) et `finance.views.report_csv` (période). Ils n'utilisent pas les mêmes agrégats (le premier lit `Operation.fee`, le second le grand livre).
- Pas de rapport de trésorerie, de fonds apportés, de charges, investisseurs, partenaires, actionnaires, ni de rapport de contrôle. Pas de statut provisoire / clôturé / révisé, pas d'identifiant + version dans l'en-tête.
- Convention de semaine non définie.

### 2.6 Devises (brief §5)
- Comptes mono-devise, écritures équilibrées par devise : conforme. Une caisse agent = un compte par devise (contrainte legacy `one_account_per_agent_currency`).
- Aucune écriture de **change** (deux montants + taux + frais + écart) : à ajouter au moteur, sans inventer son traitement dans la distribution.

## 3. Plan de mise à jour (lots)

### Lot A — Fonds, origine, allocation, transit (modèles + services + tests)
- `FundContribution` : origine (SHAREHOLDER / INVESTOR / PARTNER_GUARANTEE / OPENING), partie, montant, devise, date de réception, compte de réception, lot d'écriture unique (idempotence par `source`).
  - Actionnaire → crédit `CAPITAL-<devise>-<partie>` ; investisseur → crédit `INVESTOR_FUNDS-<devise>-<partie>` (passif) ; garantie → crédit `PARTNER_GUARANTEE-<devise>-<partie>` (passif).
- `InternalTransfer` : source, destination, montant, frais, statut INITIATED / CONFIRMED / CANCELLED ; l'initiation crédite la source et débite `TRANSIT` ; la confirmation débite la destination et crédite `TRANSIT`. Total conservé hors frais.
- `PartnerGuarantee` : plafond par opération, exposition courante, contrôle à la création d'opération.
- `Operation.supplier_fee` : charge FINCORYA distincte de la commission ; n'entre jamais dans la base 60/40.

### Lot B — Résultat, distribution, demandes
- `DistributionPolicy` datée : mode `EQUAL_SHARES` (nouvelle règle) ou `RULE_PERCENT` (ancienne règle 25 %), date d'entrée en vigueur, éligibilité (actionnaires actifs à la clôture). L'ancienne règle reste appliquée aux périodes déjà clôturées.
- Calcul du résultat mensuel : commissions propres + part FINCORYA − salaires − autres charges − frais fournisseurs − rémunérations investisseurs − pertes antérieures reportées. Perte → aucune distribution, report sur `ProfitPeriod.loss_carried_forward`.
- Répartition égale avec arrondi au centime : la dernière part absorbe le reste ; somme des parts = montant distribué.
- `StakeholderRequest` : REINVEST_PROFIT (total/partiel), WITHDRAW_PROFIT, INCREASE_INVESTMENT, WITHDRAW_INVESTMENT, INCREASE_GUARANTEE, CONVERT_COMMISSION_TO_GUARANTEE ; statuts SUBMITTED / APPROVED / REJECTED / EXECUTED ; exécution idempotente ; aucune écriture avant validation.

### Lot C — Permissions serveur
- Extension de `FinancePolicy` : `own_party_only` pour SHAREHOLDER / INVESTOR / PARTNER, `global_read_only` pour l'actionnaire, `weekly_report` pour la chargée des finances.
- Filtre serveur des rapports, exports, endpoints HTMX et agrégats par partie liée (`Stakeholder.owner == request.user`).

### Lot D — Centre de rapports
- Un seul module d'agrégats (`finance/reporting.py`) lu par le tableau de bord, le PDF, l'Excel et le CSV.
- Rapports : activité, fonds apportés, trésorerie, commissions, charges, investisseurs, partenaires, actionnaires, résultat mensuel, contrôle.
- En-tête : type, période + fuseau, filtres, devise et taux, date de génération, auteur, statut, identifiant + version, pagination.
- Convention de semaine configurable (`FINANCE_WEEK_START`, défaut lundi) et affichée.
- Échappement des cellules Excel/CSV commençant par `= + - @`.

### Lot E — Interfaces
- Espaces actionnaire, investisseur, partenaire (lecture + demandes), gestion des apports, transferts internes, demandes et validations, centre de rapports.
- Rafraîchissement HTMX avec heure de dernière actualisation.

### État d'avancement

| Lot | Statut | Livré |
|---|---|---|
| A — Fonds, allocation, transit, partenaires | Livré | `FundContribution`, `InternalTransfer`, `PartnerGuarantee`, `Operation.supplier_fee`, `finance/funds.py`, contrôle du plafond dans `operations/services.py` |
| B — Résultat, distribution, demandes | Livré | `DistributionPolicy`, `ProfitPeriod.prior_losses / distributable`, `finance/results.py`, `StakeholderRequest` + workflow |
| C — Permissions serveur | Livré | `FinancePolicy` étendu (SHAREHOLDER / INVESTOR / PARTNER), `linked_party`, périmètre des rapports et exports |
| D — Centre de rapports | Livré (v1) | `finance/reporting.py` (10 rapports, en-tête complet), CSV / XLSX / PDF via `reports/services.py`, échappement des formules, écran HTMX |
| E — Interfaces | Livré (v1) | Espace partie prenante, demandes et validations, apports, transferts, garanties, politiques ; guide `docs/finance-workflows.md` |
| Tests | 104 tests verts (SQLite) | `tests/test_finance_funds.py`, `test_finance_access.py`, `test_finance_screens.py` couvrent les critères 1–17 et 19–22 du brief ; le critère 6 (concurrence) reste couvert par la suite PostgreSQL existante ; le critère 18 (taux quotidiens vs participations) est garanti par construction (participations lues dans le grand livre, jamais converties) |

Points restant ouverts : nouvelle caisse agent créée après la bascule (nécessite une reprise réconciliée avant tout mouvement — comportement existant, à outiller), écritures de change (A2), calcul automatique des rémunérations investisseurs (A1).

## 4. Ambiguïtés identifiées (pas de supposition silencieuse)

| # | Point | Traitement retenu en attendant la réponse |
|---|---|---|
| A1 | Rémunération des investisseurs : montant fixe, % du capital investi ou % du résultat ? | `Stakeholder.investor_return_percent` existe (rendement annuel). Le calcul du résultat déduit les rémunérations **comptabilisées comme charges** (catégorie `INVESTOR_RETURN`), jamais un montant déduit deux fois. La règle de calcul automatique reste à confirmer par l'admin. |
| A2 | Écarts de change et réévaluation | Tracés séparément (compte `FX_DIFFERENCE`), exclus du résultat distribuable tant que l'admin n'a pas confirmé leur traitement. |
| A3 | Actionnaire également agent (Ruth) | Le rôle agent donne accès à sa caisse ; le rôle actionnaire donne la lecture globale. Les deux se cumulent sans droit de modification global. |
| A4 | Position négative mentionnée dans le brief | Non convertie en écriture. Reprise manuelle à documenter par l'admin après vérification (voir annexe historique du guide). |
| A5 | Convention de semaine | Lundi → dimanche, fuseau Europe/Istanbul, paramétrable. |
| A6 | Date d'entrée en vigueur de la répartition égalitaire | Paramètre `DistributionPolicy.effective_from` à saisir par l'admin ; par défaut aucune politique → clôture mensuelle refusée tant qu'elle n'est pas définie. |

## 5. Paramètres à renseigner par l'admin après déploiement

1. `DistributionPolicy` : mode `EQUAL_SHARES`, date d'entrée en vigueur.
2. Rattachement des actionnaires actifs (`Stakeholder` type SHAREHOLDER, `is_active`, `owner` = utilisateur).
3. Garantie et plafond par opération de chaque partenaire (`PartnerGuarantee`).
4. Rendement et fréquence de chaque investisseur (`Stakeholder.investor_return_percent`, `payment_frequency`) et règle de calcul (A1).
5. Devise de consolidation et taux figés pour les rapports multidevises.
6. Convention de semaine (`FINANCE_WEEK_START`).
7. Droits de téléchargement par rôle (`REPORT_DOWNLOAD_ROLES`).
8. Origine des 1 540 USD de cash en main et nature de la position Fantiny (reprise manuelle).
