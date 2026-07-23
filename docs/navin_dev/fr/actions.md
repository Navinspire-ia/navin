# Menu Actions

Le bouton **Actions** (icône checklist, à côté du sélecteur de projet) ouvre un menu d'audits et de corrections en un clic. Chaque action construit une instruction précise et l'envoie automatiquement au chat de l'agent.

## Options

- **Périmètre** — appliquer à **tout le projet** ou seulement au **fichier actif** (le fichier ouvert dans l'éditeur).
- **Auto-fix** — coché, l'agent ne s'arrête pas au rapport : il applique les corrections confirmées et les vérifie. Décoché, il propose les corrections classées par impact sans toucher au code.

## Actions disponibles

### Qualité

| Action | Envoie | Ce qui se passe |
| --- | --- | --- |
| Code review | `/inspect <scope>` | Revue complète : bugs, régressions, problèmes de sécurité, tests manquants, maintenabilité — avec citations fichier/ligne. |
| Tests | prompt | Exécute la suite de tests du périmètre, rapporte les échecs avec leurs causes ; avec auto-fix, répare code/tests jusqu'à ce que toute la suite passe. |
| Quality gate | prompt | Lint + vérification de types + tests + scan sécurité rapide + odeurs de performance, résumés en tableau pass/fail. |

### Sécurité

| Action | Envoie | Ce qui se passe |
| --- | --- | --- |
| Audit sécurité | `/fortify <scope>` | Auth, validation des entrées, injections, secrets, défauts dangereux, risques de dépendances — notés critical→low. |
| Vulnérabilités | `/probe <scope>` | Patterns OWASP Top 10, secrets en dur, dépendances vulnérables, SSRF/path traversal, injection de prompt. |

### Performance

| Action | Envoie | Ce qui se passe |
| --- | --- | --- |
| Performance | `/turbo <scope>` | Chemins chauds, requêtes N+1, I/O bloquantes, caches, taille de bundle, mémoire — mesurés avant recommandation. |
| Supervision | `/pulse <scope>` | Métriques de santé : complexité, fraîcheur des dépendances, lint, couverture, dette — tableau de bord noté. |

### Design & UX

| Action | Ce qui se passe |
| --- | --- |
| Audit UX/UI | Parcours utilisateur, clarté de navigation, états vide/chargement/erreur, espacements, responsive, feedback d'interaction — classés par impact utilisateur. |
| Design visuel | Palette, échelle typographique, variantes de composants, bordures/rayons/ombres, mode sombre, design tokens — incohérences avec proposition unifiée. |
| Accessibilité | WCAG 2.2 AA : contraste, navigation clavier, gestion du focus, ARIA, labels de formulaires, textes alternatifs, parcours lecteur d'écran — violations par sévérité. |

### Maintenance

| Action | Ce qui se passe |
| --- | --- |
| Refactoring | Code mort, duplication, fonctions/composants surdimensionnés, dépendances enchevêtrées, nommage — classés par gain vs risque. |
| Documentation | Exactitude du README, instructions d'installation, docstrings manquantes, docs d'API, sections obsolètes ; avec auto-fix, écrit directement la documentation manquante. |

## Exemple ciblé fichier

Ouvrez `src/App.tsx` dans l'éditeur, passez le périmètre sur **fichier**, cliquez **Code review** → l'agent reçoit `/inspect /chemin/du/projet/src/App.tsx` et ne passe en revue que ce fichier.
