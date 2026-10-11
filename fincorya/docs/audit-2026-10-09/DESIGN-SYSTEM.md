# FINCORYA — Design system

**Version 1.0 · Complété le 11 octobre 2026 · Référence de l’audit du 9 octobre**

## 1. Périmètre et sources

Ce catalogue décrit l’identité existante et propose des règles de consolidation. Il accompagne le [PRD](PRD.md), le [TRD](TRD.md) et les [parcours](APP-FLOW.md). Les tokens et la [prévisualisation](design-preview.html) sont des livrables documentaires autonomes ; leur présence ne signifie pas leur intégration à l’application.

Sources locales : [référence de marque](../../design-system/fincorya/MASTER.md), [styles sources](../../static_src/css/interface.css), [template de base](../../templates/base.html). Les sept feuilles globales sont chargées dans l’ordre fincorya, enterprise, workflow, cash, brand, system, interface. Conserver cet ordre tant que les dépendances de cascade ne sont pas résolues. Modifier les sources `static_src`, puis suivre la chaîne de collecte du projet ; ne pas traiter une copie collectée comme source principale.

## 2. Identité et tokens

| Usage observé | Valeur | Règle d’emploi |
|---|---|---|
| Action principale | `#006B4F` | Boutons principaux, liens, focus sur fond clair |
| Navigation | `#013B36` | Sidebar et titres forts |
| Accent or | `#C9A227` | Accent décoratif limité, pas texte courant sur blanc |
| Fond | `#F7F8F5` | Surface générale |
| Surface | `#FFFFFF` | Formulaires et panneaux |
| Texte | `#18201F` | Contenu principal et montants |
| Texte secondaire | `#5F6B67` | Explications, libellés secondaires |
| Séparateur | `#DCE3DF` | Séparation décorative ; insuffisant seul pour identifier un champ |
| Erreur | `#C0392B` | Message accompagné d’un texte explicite |

Les [tokens JSON](design-tokens.json) et [CSS](design-tokens.css) partagent les mêmes noms et valeurs. Le préfixe `--audit-` isole cette référence. Les couleurs de statuts reprennent les couples du CSS existant. Le contour de champ `#5F6B67` est une **cible proposée**, plus distincte que le séparateur existant. Les espacements 4, 8, 12, 16, 24 et 32 px constituent une échelle de consolidation proposée.

## 3. Typographie et composition

Inter pour le texte, Space Grotesk pour les titres, JetBrains Mono pour les références ; les familles de secours doivent préserver la lisibilité. L’application charge Inter localement et les deux autres familles via Google Fonts. La prévisualisation utilise les familles de secours disponibles sans requête externe.

La sidebar existante mesure 232 px, la barre supérieure 76 px. Les panneaux privilégient une bordure fine, un rayon de 8 à 10 px et une ombre discrète. La référence historique prévoit une navigation repliable sous 820 px ; les feuilles existantes comportent aussi des adaptations à 1024 et 560 px. Il ne s’agit pas d’un breakpoint universel déjà harmonisé.

**Cible de consolidation :** corps à 16 px, aide à 14 px, titres à 24–36 px, interligne 1,5. Le code existant contient plusieurs textes à 11–13 px ; cette cible ne les décrit pas comme corrigés. Les montants utilisent des chiffres tabulaires, un alignement à droite dans les tableaux et une devise explicite. Ne jamais sommer USD et CDF sans conversion documentée.

## 4. Composants et contrats

| Composant | Contenu et comportement attendus |
|---|---|
| Navigation | Page active nommée, `aria-current`, lien d’évitement, accès clavier |
| Bouton | Verbe précis, cible de 44 px minimum, focus visible ; une action principale par étape |
| Champ | Label permanent, aide liée, unité visible, erreur liée avec `aria-describedby` et `aria-invalid` |
| Montant | Devise, précision cohérente, distinction entre saisi, principal, frais et net |
| Tableau | Caption, en-têtes associés, conteneur défilant sur mobile accessible au clavier |
| Statut | Texte et couleur ; le texte doit décrire l’objet concerné |
| Message | Résultat réel, prochaine étape et acteur requis ; annonce non intrusive des changements |
| État vide | Explication et lien de retour ; création proposée seulement si autorisée |
| Confirmation | Objet, montant/devise, effet et caractère définitif expliqués avant l’action |
| Chargement | Empêcher les soumissions répétées côté interface ; idempotence garantie côté serveur |

Les boutons cachés ne remplacent jamais les contrôles serveur. Une notification envoyée à une file n’est pas présentée comme reçue. Une simulation ne doit pas afficher une référence laissant croire qu’une écriture a été enregistrée.

## 5. Statuts et langage financier

| Famille visuelle | Couples texte / fond observés | Exemples à contextualiser |
|---|---|---|
| Succès | `#116C4E` / `#E7F6EF` | Opération terminée ; transfert confirmé |
| Attente | `#825814` / `#FFF5DF` | Charge en attente ; transfert initié |
| Erreur / refus | `#9C3027` / `#FCECEA` | Export échoué ; demande rejetée |
| Information | `#1E5F8F` / `#E8F1FB` | Transfert reçu, encore en transit |

Une charge APPROVED est reconnue, pas nécessairement payée. Un transfert RECEIVED reste en transit jusqu’à CONFIRMED. Une clôture REVIEW verrouille la caisse sans corriger automatiquement le solde. OPEN/LOCKED décrit une période, pas une caisse. Conserver ces distinctions dans badges, confirmations, filtres et exports.

La prévisualisation illustre uniquement FLOW-04 : comptage identique → CLOSED ; écart justifié → REVIEW ; correction administrative simulée → CLOSED. Elle expose le solde théorique initial et le solde après correction séparément. Elle ne modélise ni droits réels, ni concurrence, ni une nouvelle saisie de comptage rejeté.

## 6. Accessibilité : cible et recette

Ces règles sont des critères internes de recette, pas une certification de conformité. Vérifier les contrastes des couples réellement rendus, notamment hover, focus, erreur et texte secondaire. Cibles : 4,5:1 pour texte courant et 3:1 pour repères interactifs et texte de grande taille. L’or et les bordures pâles ne conviennent pas à tous ces usages.

Parcourir au clavier navigation, formulaire, erreurs, tableau et actions ; aucun piège de focus. Vérifier les labels et annonces au lecteur d’écran, le zoom à 200 %, la largeur à 320 px et les préférences de mouvement réduit. Ne jamais transmettre un état uniquement par couleur. Une région de statut ne doit pas réannoncer toute la page à chaque saisie.

Pour l’application, compléter ces vérifications par rôle sur les écrans réels ; une prévisualisation documentaire ne prouve pas leur accessibilité. Tester en particulier les états vides et refusés décrits dans FLOW-10 et les critères PRD AC-04, AC-05 et AC-10.

## 7. Gouvernance et livraison

Chaque changement doit préciser : source observée ou proposition, composant concerné, impact mobile/clavier et parcours affecté. Modifier ensemble JSON, CSS et exemples. Conserver le logo officiel et les règles financières datées. Les exceptions métier restent dans le PRD ; elles ne se règlent pas par une variante graphique.

La prévisualisation s’ouvre directement depuis `design-preview.html`, avec `design-tokens.css` dans le même dossier. Aucun serveur, compte, stockage ou service externe n’est nécessaire. Les valeurs de démonstration sont fictives et réinitialisables. Les validations structurelles ne remplacent pas une recette visuelle dans un navigateur.
