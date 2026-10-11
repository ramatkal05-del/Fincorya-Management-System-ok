# FINCORYA — Audit du système existant

**9 octobre 2026 · État local de travail · Audit technique et produit**

## 1. Conclusion

**La base fonctionnelle est substantielle, mais une nouvelle livraison complète ne peut pas encore être qualifiée comme validée.** Les écarts les plus importants concernent la cohérence de l'authentification sans MFA, la transition entre les deux moteurs financiers et la reproductibilité des tests. Le MFA désactivé n'est pas un défaut : c'est la décision produit confirmée par le propriétaire.

Appréciation de préparation à une livraison : **65/100, stabilisation requise**. Ce score est un jugement de triage, pas une mesure de sécurité ni une certification. Il reflète les preuves locales, les sept échecs de tests et l'absence de validation PostgreSQL/déploiement dans cette session. Il ne juge pas une instance distante non inspectée.

## 2. Méthode et limites

Inspection des routes, permissions, formulaires, services transactionnels, modèles, rapports, notifications, templates, feuilles de style, configuration Render, CI et documentation. Les sources modifiées et non suivies font partie du périmètre. Aucun import historique, script de clôture, réinitialisation, envoi d'e-mail ou migration de la base métier n'a été exécuté.

Les [preuves JSON](evidence.json) contiennent les empreintes de 275 fichiers sources, l'inventaire des routes/modèles et les résultats des sondes. Les contrôles portent sur Python **3.14.3** et Django **5.2.17** dans `.audit-venv`. Le runtime du manifeste de déploiement est Python **3.11.9** : l'équivalence entre ces environnements reste à vérifier.

La revue visuelle est une **revue de sources CSS/templates**, complétée par des calculs de contraste et un spécimen documentaire. Elle ne vaut pas recette de toutes les pages rendues, à toutes les tailles.

## 3. Cartographie de maturité

| Domaine | Réalité observée | Réserve |
|---|---|---|
| Identités | Six rôles, gestion admin, activation, profil, limitation des tentatives | Parcours MFA résiduels incohérents |
| Opérations | Envois, reçus à payer, retraits, commissions manuelles, annulations/révisions | Respecter frais ajoutés/déduits et historique |
| Caisses | Globale/agents, allocation, remises, clôture et revue des écarts | Agent peut clôturer sa propre caisse contrairement à certains textes anciens |
| Grand livre | Lots équilibrés par devise, publication, contre-passation, caches réconciliables | Activation et reprise nécessaires ; pas validées ici sur les données réelles |
| Apports et partenaires | Réception, rapprochement historique, garanties et règles datées | Les données importées ne sont pas des apports confirmés par défaut |
| Résultat/distribution | Clôture mensuelle, pertes reportées, politique datée, paiements | Ancien 60/20/20 retiré ; règle nouvelle à quatre bénéficiaires |
| Rapports | Écran/PDF/XLSX/CSV, périmètres par rôle, exports privés | Trois voies de calcul coexistent, dont une situation de septembre spécifique |
| Notifications | Stockage, worker, rappels, reprise des échecs | Worker inline dépend du cycle de vie web |
| Contrats | Modèles de contrat et versions | Pas de parcours applicatif autonome démontré par les URL inspectées |
| Interface | Marque et composants réutilisés, focus, menu adaptatif | Sept feuilles CSS globales et styles superposés |

## 4. Constats prioritaires

### AUD-01 — Configuration de production contradictoire avec MFA désactivé

**Priorité : P1 · Confirmé par sonde.** `login_view` ouvre directement une session et `mfa_required` n'exige que la connexion. Pourtant, `settings.py` refuse `ENVIRONMENT=production` avec `MFA_ENABLED=False`. `render.yaml` et `.env.example` conservent `True`.

**Conséquence :** l'opérateur ne peut pas exprimer proprement la décision produit par le paramètre prévu. Une valeur `True` ne signifie plus que la connexion impose un second facteur.

**Preuves :** [vues d'authentification](../../apps/accounts/views.py), [configuration](../../config/settings.py), [manifeste Render](../../../render.yaml). Sonde : import des settings avec production/MFA désactivé → `ImproperlyConfigured`, sans connexion à la base.

**Correction cible :** rendre cohérents paramètre, garde de démarrage, noms des décorateurs, écrans et documentation, en conservant la connexion e-mail/mot de passe sans MFA. Ne pas réactiver le MFA pour satisfaire les anciens tests. **Recette :** démarrage production avec MFA désactivé accepté ; visite anonyme refusée ; séparation des rôles conservée. Voir PRD-01 et TRD-02.

### AUD-02 — Changement de mot de passe bloqué sans TOTP

**Priorité : P1 · Confirmé par sonde et test.** `SecurePasswordChangeForm.mfa_token` est requis au niveau du champ. `clean_mfa_token` prévoit pourtant un cas sans TOTP ; la validation du champ échoue avant d'atteindre cette branche lorsque le code est absent.

**Conséquence :** un utilisateur sans TOTP ne peut pas changer son mot de passe par le formulaire normal sans renseigner artificiellement ce champ.

**Preuve :** [formulaire](../../apps/accounts/forms.py), sonde `User(totp_enabled=False)` avec ancien et nouveau mots de passe valides → erreur `mfa_token: required`.

**Correction cible :** supprimer cette obligation du parcours sans MFA, préserver la vérification de l'ancien mot de passe, la validation du nouveau et le maintien de session. Le traitement d'un éventuel second facteur optionnel résiduel est une décision distincte, pas un prérequis à réintroduire. Ajouter une recette dédiée à un compte historiquement équipé de TOTP avec MFA globalement désactivé.

### AUD-03 — Anciennes attentes MFA dans les tests

**Priorité : P1 pour la qualification · Écart de spécification.** Cinq échecs portent sur des redirections vers TOTP/vérification ou l'envoi d'un OTP après connexion. Ils sont incompatibles avec la décision produit actuelle.

**Preuves :** `AdminMFARegressionTests.test_password_only_session_cannot_access_admin` et quatre cas de `AuthenticationFlowTests` dans [tests d'authentification](../../tests/test_authentication.py) et [régressions](../../tests/test_audit_regressions.py).

**Correction cible :** remplacer les assertions obsolètes par des tests du parcours sans MFA ; conserver les assertions de session, autorisations, limitation des tentatives et accès administrateur. Ne pas simplement supprimer toute couverture d'authentification.

### AUD-04 — Lien permanent vers une situation de septembre absente

**Priorité : P2 · Reproduit dans la suite.** Le centre affiche un lien vers `/rapports/situation-septembre-2026/`. La vue attend un `ImportRow` spécifique et renvoie 404 s'il manque.

**Conséquence :** une base neuve possède un lien de navigation cassé. Le rapport est également attaché à un mois fixe.

**Preuves :** [vue](../../apps/reports/views.py), [centre](../../templates/reports/center.html), [calcul](../../apps/reports/month_end.py), échec du test de navigation administrateur.

**Correction cible :** afficher un état vide utile ou conditionner le lien à une déclaration disponible ; proposer ensuite une sélection de mois. Conserver la distinction « déclaré » / « enregistré » et les écarts, sans fabriquer de chiffres. Voir PRD-11.

### AUD-05 — Déploiement prévu en mode historique, bénéfices historiques retirés

**Priorité : P1 · Incohérence de configuration et de compatibilité constatée.** Le manifeste fixe `FINANCE_LEDGER_ENABLED=False`, alors que les fonctions historiques de calcul/distribution lèvent désormais une erreur invitant à utiliser Finance. Les nouveaux workflows d'apports/transferts exigent une bascule active.

**Conséquence :** le déploiement décrit dans le dépôt ne suffit pas à garantir un parcours complet de bénéfices et d'apports. Cela ne prouve pas le paramétrage réel d'une instance distante.

**Preuves :** [fonctions historiques](../../apps/profits/services.py), [`_engine_required`](../../apps/finance/funds.py), [bascule](../../apps/finance/cutover.py), [manifeste](../../../render.yaml).

**Correction cible :** documenter et tester un mode nominal unique ; préparer la reprise, comparer les soldes, approuver l'empreinte, puis activer le grand livre. L'activation seule n'est pas une reprise comptable. Tester aussi les écrans hérités et rendre leur indisponibilité explicite. Aucun changement de ce flag n'est effectué par l'audit.

### AUD-06 — Dépendance de tests PDF non déclarée et runtime local cassé

**Priorité : P1 pour la reproductibilité · Inspection statique.** `test_report_visual_check.py` importe `fitz` (PyMuPDF), absent de `requirements.txt`. La CI n'installe que ce fichier avant `manage.py test`. Le `.venv` local pointe par ailleurs vers un Python 3.11 introuvable ; l'audit utilise l'environnement alternatif.

**Conséquence :** la réussite dans l'environnement d'audit ne garantit pas qu'un environnement propre puisse collecter tous les tests. L'échec d'une exécution distante CI n'a pas été observé ici.

**Correction cible :** déclarer les dépendances de test, reconstruire un environnement Python cohérent avec le déploiement, installer depuis zéro et exécuter la CI. [CI](../../../.github/workflows/ci.yml), [dépendances](../../requirements.txt), [test PDF](../../tests/test_report_visual_check.py).

### AUD-07 — Vérifications de concurrence non exécutées

**Priorité : P1 avant qualification du moteur financier · Limite de preuve.** Trois tests sont ignorés sous SQLite. Le mutex transactionnel et `select_for_update` nécessitent PostgreSQL pour démontrer leurs garanties concurrentes.

**Correction cible :** exécuter la suite sur une base PostgreSQL de test dédiée ; vérifier doubles publications, paiements concurrents et course clôture/publication. Aucune absence de bug concurrent n'est revendiquée. [Mutex](../../apps/finance/locking.py), [tests de fondation](../../tests/test_finance_foundation.py), [workflows](../../tests/test_finance_workflows.py).

### AUD-08 — Sources de reporting et conventions temporelles à harmoniser

**Priorité : P2 · Risque étayé par le code, divergence chiffrée non démontrée ici.** Le centre utilise `finance.reporting.build_report`, le consolidé `monthly_financial_snapshot`, et la déclaration de septembre `build_month_end_statement`. Cette dernière calcule la part déclarée à partir d'un taux global du document ; les commissions opérationnelles utilisent des taux figés par opération. Ces chiffres n'ont donc pas toujours la même nature.

Autre cas précis : l'accès agent compare la date à `timezone.localdate()` alors que les bornes du rapport utilisent `BUSINESS_TIME_ZONE`. Avec UTC pour `TIME_ZONE`, le jour métier peut avoir commencé avant le jour UTC.

**Correction cible :** nommer chaque source, unifier les agrégats qui doivent être identiques et tester les changements de taux et les frontières de jours. Une déclaration humaine doit rester distincte d'un résultat comptable clôturé. [Reporting](../../apps/finance/reporting.py), [consolidé](../../apps/reports/services.py), [déclaration](../../apps/reports/month_end.py).

### AUD-09 — Documentation et règles actuelles divergent

**Priorité : P2 · Confirmé par inspection.** Des documents présentent encore MFA obligatoire, partage 60/20/20 ou modes de distribution disponibles. Le code impose aux nouvelles politiques quatre actionnaires à 25 %, retire le 60/20/20 et permet à l'agent de clôturer sa propre caisse. Le commentaire du fichier d'environnement annonce un mot de passe admin imprimé en l'absence de secret, alors que la commande refuse de démarrer.

**Correction cible :** utiliser le présent dossier comme état de référence daté, puis aligner les guides opérationnels après arbitrage des règles encore non confirmées. Sources : [README historique](../../README.md), [workflows](../finance-workflows.md), [politiques](../../apps/finance/policies.py), [caisse](../../apps/cash/services.py), [création admin](../../apps/accounts/management/commands/create_admin.py).

### AUD-10 — Cohérence visuelle et accessibilité à consolider

**Priorité : P2 · Revue statique.** Sept CSS globaux sont chargés en cascade. La référence historique annonce un seuil mobile de 820 px, tandis que menu JavaScript et CSS courants utilisent 1024 px. Inter est local ; Space Grotesk et JetBrains Mono sont chargées via Google Fonts. Les contrastes or/blanc (**2,42:1**) et couleur warning/blanc (**3,64:1**) demandent des usages limités ; ils ne conviennent pas au texte courant selon la cible interne 4,5:1.

**Correction cible :** centraliser les tokens, conserver les badges sémantiques plus contrastés, utiliser l'or pour l'accent décoratif et réaliser une recette clavier/mobile. Aucun défaut de contraste d'un écran entier n'est déduit de ces seuls couples de couleurs. Voir [design system](DESIGN-SYSTEM.md).

### AUD-11 — Exploitation à qualifier avant livraison

**Priorité : P2 · Inspection, sans panne constatée.** Le worker inline démarre depuis `AppConfig.ready()` si activé, sans garde explicite contre les commandes de gestion. Le build effectue déjà des migrations, répétées en `preDeployCommand`. Le mutex global privilégie l'intégrité au débit. Des blocs de notification absorbent les exceptions sans journal local dans le bloc.

**Correction cible :** isoler les phases build/migration, désactiver le worker pendant les commandes de maintenance/tests, mesurer sa file et ses échecs, qualifier le débit et tester une restauration base + fichiers privés. Les documents locaux décrivent des sauvegardes ; aucune restauration de ces sauvegardes n'a été démontrée ici. [Build](../../../build.sh), [notifications](../../apps/notifications/apps.py), [clôture](../../apps/finance/closing.py).

## 5. Résultats de vérification

| Contrôle | Résultat | Portée |
|---|---|---|
| Django `check` | Réussi | Revérifié dans ce dossier |
| `makemigrations --check --dry-run` | Aucun changement détecté | Cohérence modèles/migrations, pas preuve d'application en production |
| Suite précédente | 188 réussis, 7 échecs, 3 ignorés / 198 | SQLite isolé, résultats conservés de cette session |
| Import production, MFA désactivé | Refus confirmé | Aucun accès DB |
| Mot de passe sans TOTP | Champ MFA requis confirmé | Formulaire isolé, utilisateur non persisté |
| Contrastes palette | Calculés | Couples explicites dans `evidence.json` |
| Production, soldes, SMTP, concurrence PostgreSQL | Non vérifiés | Aucune certification implicite |

Décomposition des sept échecs : **5 attentes MFA obsolètes + 1 défaut de formulaire de mot de passe + 1 rapport absent**. Les tests financiers réussis sont utiles, mais les tests ignorés et l'activation conditionnelle interdisent de conclure à une validation complète en production.

## 6. Plan d'action vérifiable

| Lot | Travaux | Responsable proposé | Critère de sortie |
|---|---|---|---|
| A — Authentification | AUD-01 à 03 | Backend + produit | Connexion/profil/mot de passe sans MFA cohérents, tests verts |
| B — Mode financier nominal | AUD-05, 07, 09 | Backend + administrateur métier | Reprise répétée en environnement de recette, écarts nuls, règles signées |
| C — Rapports | AUD-04, 08 | Backend + finance | Base vide utilisable, périodes/taux/exports rapprochés |
| D — Livraison | AUD-06, 11 | Exploitation | Installation propre, suite PostgreSQL verte, restauration démontrée |
| E — Interface | AUD-10 | Frontend + recette | Tokens consolidés, parcours mobile et clavier validés |

**Action immédiate recommandée :** réaliser le lot A selon la décision sans MFA, puis exécuter la qualification PostgreSQL avant toute bascule de données réelles. Cette proposition n'est pas une correction déjà appliquée.
