# FINCORYA — Dossier produit et technique

**Version 1.0 · 9 octobre 2026 · Langue : français**

Ce dossier décrit l'application présente dans le répertoire de travail, y compris les modifications non commitées. Il intègre la décision explicite du propriétaire : **le MFA a été désactivé volontairement**. Aucun changement de code métier, de configuration, de données financières ou de déploiement n'est réalisé par ce dossier.

## Documents

| Livrable | Contenu | Lecteurs |
|---|---|---|
| [Audit du système](AUDIT.md) | Constats vérifiables, priorités, limites et plan de remédiation | Propriétaire, responsable technique |
| [App Flow](APP-FLOW.md) | Navigation, acteurs, écrans, transitions, erreurs et flux financiers | Produit, design, développement |
| [TRD](TRD.md) | Architecture, modèle de données, contrats techniques, tests et exploitation | Développement, exploitation |
| [Design system](DESIGN-SYSTEM.md) | Identité, tokens, composants, états, accessibilité, gouvernance | Design, frontend |
| [PRD](PRD.md) | Objectifs, périmètre, exigences, critères d'acceptation et priorités | Propriétaire, produit, recette |
| [Prévisualisation interactive](design-preview.html) | Palette, composants et simulateur pédagogique de parcours | Tous |
| [Tokens JSON](design-tokens.json) / [CSS](design-tokens.css) | Référence réutilisable, non intégrée à l'application | Design, frontend |
| [Preuves techniques](evidence.json) | Sondes, inventaire des routes/modèles, contrastes et empreintes des sources | Audit, développement |

## Comment lire les statuts

- **Confirmé utilisateur** : décision explicitement donnée dans cette conversation.
- **Implémenté** : comportement lu dans le code ; ce libellé ne certifie pas son activation en production.
- **Vérifié** : contrôle exécuté dans un environnement isolé, ou résultat de tests obtenu dans cette session.
- **Cible proposée** : exigence de stabilisation ou évolution ; ne signifie pas « déjà livrée ».
- **À arbitrer** : décision métier ou opérationnelle non déductible de façon certaine.

Les constats portent les identifiants `AUD-xx`, les exigences produit `PRD-xx`, les exigences techniques `TRD-xx` et les parcours `FLOW-xx`. Les documents se renvoient mutuellement pour éviter des règles contradictoires.

## Périmètre de preuve

Base : branche locale `Fincorya`, dernier commit observé `63c5116`, état de travail modifié. L'inventaire automatique recense **69 routes nommées hors administration Django** et **56 classes de modèles héritant directement de `models.Model`** ; `User`, dérivé d'`AbstractUser`, n'est pas inclus dans ce second chiffre. Il ne s'agit ni de 69 écrans ni d'un inventaire de toute l'administration.

La suite exécutée au tour précédent a produit **198 tests : 188 réussis, 7 échecs, 3 ignorés**, avec SQLite en mémoire. Les contrôles Django et l'absence de migrations manquantes ont été revérifiés pour ce dossier. Les sondes additionnelles confirment deux incohérences MFA. Les anciennes attentes MFA sont distinguées des défauts actuels.

Ce dossier ne certifie pas les soldes réels, le déploiement distant, les performances en charge, la délivrabilité des e-mails ni le comportement concurrent de PostgreSQL. Les choix d'architecture sont documentés à partir du dépôt, sans recherche de produits concurrents : l'objectif est la description fidèle de FINCORYA, pas une refonte inspirée d'autres applications.

## Reproduction des sondes

Depuis la racine du dépôt, dans l'environnement observé :

```powershell
.\.audit-venv\Scripts\python.exe fincorya/docs/audit-2026-10-09/collect_evidence.py
```

Le script force une base en mémoire, désactive le worker de notification et ne lit aucun solde de la base applicative. Il régénère `evidence.json`. Il ne relance pas la suite complète : son résultat antérieur est explicitement identifié dans les preuves.

## Ordre de stabilisation recommandé

1. Aligner complètement l'authentification sans MFA et ses tests.
2. Fixer et valider la stratégie d'activation du grand livre et la compatibilité des anciens bénéfices.
3. Rendre les rapports robustes aux données absentes et rapprocher les différentes sources de calcul.
4. Rétablir une chaîne de tests reproductible sous PostgreSQL avant de qualifier la livraison.
5. Consolider les composants visuels et vérifier les parcours réels sur mobile et au clavier.
