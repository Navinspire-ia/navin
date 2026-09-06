# Compaction des sorties de commandes

Les sorties shell de l'agent (`exec`) sont compactées avant d'entrer dans le contexte du modèle. Le bruit (barres de progression, lignes répétées, tableaux énormes) est réduit ; les échecs et les status utiles sont conservés. Aucun filtre externe à installer.

Le terminal live n'est pas modifié pendant l'exécution. La compaction s'applique au résultat final reçu par le modèle.

## Commandes couvertes

| Famille | Exemples | Résultat pour le modèle |
| --- | --- | --- |
| Git | `status`, `diff`, `log`, `push` | Status groupé ; progress retiré |
| Tests | pytest, jest/vitest, cargo/go test, playwright | Échecs et résumé d'abord |
| Python | ruff, mypy, pip, uv | Findings ou résumé d'install |
| JS / build | eslint, tsc, next, npm install | Erreurs d'abord |
| Conteneurs | docker / podman | Colonnes essentielles ; logs dédupliqués |
| Kubernetes | kubectl, oc, helm | Tables compactes ; describe allégé |
| Infra | terraform, make, gradle | Erreurs et résumé |
| Réseau | curl, gh | Corps ou tables courts |
| Autre | commande non classée | Nettoyage ANSI / progress / doublons |

Les wrappers `bash -lc '…'` / `sh -c '…'` sont déroulés pour classer la commande réelle.

## Moment d'application

| Situation | Compaction |
| --- | --- |
| `exec` synchrone | Après la fin du process |
| `exec` background | Quand le process est terminé |
| Session `write_stdin` | Quand le process est terminé |
| Stream live | Aucune |

Si le texte compacté est plus court, le log complet est écrit sous `.navin/tool-results/exec/` et le résultat d'outil indique le chemin.

## Coût

La compaction réduit les tokens de **sortie d'outil** sur les sessions riches en commandes. Le system prompt, les schémas d'outils et l'historique restent. Ce n'est pas une réduction globale de facture.

Implémentation : `navin/agent/command_output.py`, branché depuis `navin/agent/tools/shell.py` et `exec_session.py`.

## Pages liées

- [Editor AI](../en/editor-ai.md)
- [Code agent](../en/code-agent.md)
- [Command output compaction](../en/command-output-compaction.md)
