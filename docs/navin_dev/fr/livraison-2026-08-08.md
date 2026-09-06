# [Interne] Livraison 2026-08-08

Note interne d'équipe - ne pas publier sur le site docs.

Récapitulatif de ce qui a été livré / stabilisé ce jour dans Navin AI v2.

Convention : tiret simple `-` uniquement.

## 1. Compaction native des sorties de commandes

Brique principale :

- Module `navin/agent/command_output.py`
- Branché sur `exec` sync, sessions background, `write_stdin` (à la fin du process)
- Couverture : git, tests, Python lint/pip, JS/build, docker, **Kubernetes/helm/oc**, terraform, make, curl, gh…
- Spool raw dans `.navin/tool-results/exec/` quand ça compacte
- Tests : unitaires + live + niveaux (stubs k8s) - skip si outil absent

Doc détaillée : [compaction-sorties-commandes.md](./compaction-sorties-commandes.md)

## 2. UI onboarding / bannière providers

- Bannière « Aucun modèle n'est prêt » : texte en pleine largeur (plus de colonne d'un mot)
- Wizard first-run : bouton **Passer** visible (étapes 1 et 2)
- Fichiers : `webui/src/App.tsx`, `webui/src/components/onboarding/FirstRunWizard.tsx`

## 3. Clarifications Free vs abonnement

- Free perso = clé utilisateur + quotas modèles gratuits (souvent « busy » / rate limit)
- Flash/Plus = clé gérée Navin + budget $ (le % d'usage n'est pas le volume de tokens Free)
- Prompt caching déjà en place pour les providers compatibles (Navin / Anthropic / passerelles)
- Response caching HTTP non retenu pour l'agent (body toujours différent)

## 4. Ports / installs (WSL vs Windows)

Rappel opérationnel documenté côté guide de test :

| Port | Rôle |
|------|------|
| 5173 | Vite dev (souvent le plus fiable depuis Windows → WSL) |
| 8766 | WebUI gateway packagé / desktop |
| 18790 / 18791 | Health gateway (pas l'UI) |

Plusieurs installs (`navin.exe`, `/usr/bin/navin`, shim `~/.local/bin/navin`, `.venv`) ne partagent pas toujours la même config `~/.navin`.

## Commandes de vérification

```bash
# Compaction
.venv/bin/python -m pytest \
  tests/test_command_output.py \
  tests/test_command_output_exec_live.py \
  tests/test_command_output_levels.py -q

# Corps blog SEO
cd site && python3 scripts/generate-blog-bodies.py

# Docs EN vers site (catalogue anglais uniquement)
cd site && npm run sync-docs
```
