# Packs de plugins

Les packs sont des paquets autonomes de **skills**, **serveurs MCP** et configuration qui étendent Navin à chaud - sans redémarrage.

## Anatomie d'un pack

```
mon-pack/
├── pack.json          # manifeste : nom, version, description
├── skills/            # un dossier par skill, chacun avec SKILL.md
│   └── mon-skill/
│       └── SKILL.md
└── mcp/
    └── servers.json   # définitions de serveurs MCP (fusionnées avec le préfixe mon-pack-)
```

- Les skills deviennent disponibles pour l'agent dès l'activation du pack (skills workspace > skills du pack > skills intégrés en cas de conflit de nom).
- Les serveurs MCP sont fusionnés dans la configuration principale sous l'espace de noms `<pack>-<serveur>` et rechargés à chaud.

## Gérer les packs

### Depuis la WebUI

**Réglages → Skills → Packs de plugins** : installation depuis une URL Git ou un chemin local, activation/désactivation par interrupteur, suppression avec confirmation.

### Depuis le chat - `/pack`

| Sous-commande | Effet |
| --- | --- |
| `/pack list` | Liste les packs installés avec statut et contenu. |
| `/pack install <url-git\|chemin>` | Installe depuis un dépôt Git ou un dossier local. |
| `/pack enable <nom>` | Active le pack (skills + serveurs MCP deviennent actifs). |
| `/pack disable <nom>` | Le désactive sans le désinstaller. |
| `/pack remove <nom>` | Désinstalle le pack et nettoie ses serveurs MCP. |

### Par l'agent

Le skill `pack-builder` apprend à l'agent à créer, auditer et gérer des packs. Vous pouvez simplement demander : *« Construis-moi un pack avec un skill pour notre API interne et un serveur MCP pour notre doc. »*

## État et stockage

- Les packs vivent dans `~/.navin/plugins/`, avec un `state.json` qui suit les statuts activé/désactivé et les serveurs MCP gérés par chaque pack.
- Désinstaller un pack retire automatiquement ses serveurs MCP préfixés de la configuration principale.
