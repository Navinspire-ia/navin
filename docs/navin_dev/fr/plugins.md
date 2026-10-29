# Plugins

Les plugins sont des paquets autonomes de **skills**, **agents**, **commandes slash**, **hooks** et **serveurs MCP** qui étendent Navin à chaud - sans redémarrage. Les plugins écrits pour Claude Code s'installent tels quels.

## Anatomie d'un plugin

```
mon-plugin/
├── plugin.json                 # manifeste : nom, version, description, auteur, site
│   (ou .claude-plugin/plugin.json, la disposition Claude Code)
├── skills/<skill>/SKILL.md     # skills, chargés à côté des skills intégrés et du workspace
├── agents/*.md                 # sous-agents, listés sous « Project Subagents »
├── commands/*.md               # commandes slash, lancées avec /mon-plugin:<commande>
├── hooks/hooks.json            # hooks de cycle de vie (PreToolUse, Stop, ...)
└── mcp.json                    # {"mcpServers": {...}}, fusionné avec le préfixe mon-plugin-
    (ou .mcp.json, ou une clé "mcpServers" dans le manifeste)
```

- Un seul composant suffit à faire un plugin valide (un plugin qui n'a que des commandes s'installe).
- `${CLAUDE_PLUGIN_ROOT}` et `${NAVIN_PLUGIN_ROOT}` désignent tous deux le dossier du plugin dans les hooks et les commandes MCP.
- Skills, agents et commandes sont pris en compte au message suivant (workspace > plugin > intégré en cas de conflit de nom). Les serveurs MCP sont fusionnés dans la configuration principale sous `<plugin>-<serveur>` et connectés immédiatement.

## Gérer les plugins

### Depuis le chat - `/plugin`

| Sous-commande | Effet |
| --- | --- |
| `/plugin` ou `/plugin list` | Plugins installés avec statut, composants, état de chaque serveur MCP (connecté / non connecté) et marketplaces enregistrées. |
| `/plugin install <source>` | Installe et détaille ce que le plugin apporte (skills, agents, noms `/plugin:commande`, hooks, serveurs MCP), puis connecte ses serveurs MCP dans l'agent en cours. |
| `/plugin uninstall <nom>` | Retire le plugin et déconnecte ses serveurs MCP. |
| `/plugin enable <nom>` / `disable <nom>` | Active ou désactive sans désinstaller ; les serveurs MCP suivent. |
| `/plugin marketplace add <owner/repo\|url-git\|chemin>` | Enregistre une marketplace (un dépôt avec `.claude-plugin/marketplace.json`). |
| `/plugin marketplace list` | Marketplaces et plugins `nom@marketplace` qu'elles proposent. |
| `/plugin marketplace update [nom]` | Rafraîchit les clones des marketplaces. |
| `/plugin marketplace remove <nom>` | Oublie une marketplace ; les plugins installés restent. |

`<source>` est l'un de :

| Source | Exemple |
| --- | --- |
| Un plugin de marketplace | `deploy@acme-tools` (ou simplement `deploy` si une seule marketplace le propose) |
| Un dépôt GitHub | `acme/tools`, ou `acme/tools#plugins/deploy` pour un dossier d'un monorepo |
| Une URL git | `https://git.example/tools.git` |
| Un dossier local | `~/dev/mon-plugin` |
| Un paquet npm | `@acme/mon-skill` (récupéré avec `npm pack`, rien n'est exécuté) |

`/pack` reste un alias de `/plugin`.

### Depuis la WebUI

**Réglages → Skills → Plugins** : installation depuis une URL Git, un `owner/repo` GitHub, un plugin de marketplace, un chemin local, un paquet npm ou une archive téléversée ; activation/désactivation par interrupteur ; suppression avec confirmation.

### Par l'agent

Le skill `pack-builder` apprend à l'agent à créer, auditer et gérer des plugins. L'outil `mcp` ajoute, liste et retire des serveurs MCP à chaud. Vous pouvez simplement demander : *« Installe le plugin acme/tools »* ou *« Construis-moi un plugin avec un skill pour notre API interne et un serveur MCP pour notre doc. »*

## Serveurs MCP

- Un serveur déclaré avec une `url` est connecté en streamable HTTP ; si le point d'entrée répond 405 (serveur SSE uniquement), Navin réessaie seul en SSE et le dit dans le journal. Mettre `"type": "sse"` évite la sonde.
- Un serveur stdio (`npx`, `uvx`, `bunx`, `docker`, `node`, `python`) tourne comme un processus local avec vos droits ; sur Windows les shims `.cmd` sont résolus pour vous.
- Un serveur qui ne répond pas coûte une tentative de connexion par fenêtre de repos, jamais un chat figé.

## État et stockage

- Les plugins vivent dans `~/.navin/plugins/<nom>/` ; `state.json` suit les statuts activé/désactivé et les serveurs MCP de chaque plugin ; `marketplaces.json` liste les marketplaces et leurs clones sous `.marketplaces/`.
- Désinstaller un plugin retire automatiquement ses serveurs MCP préfixés de la configuration principale.
- Une politique d'organisation (`allowManagedPluginsOnly`) peut bloquer plugins et serveurs MCP ; `/plugin list` les montre comme bloqués par la politique.
