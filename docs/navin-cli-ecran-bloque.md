# Navin CLI : ecran bloque

Tu as lance `.venv/bin/navin-cli`. C'est le TUI Navin (Python -> `navin.cli.commands.run_cli` -> `navin/tui`).

Ce n'est pas Windows, pas Cursor, pas Azure / client id.

## Ce qui s'est passe

L'agent dans ce CLI a lance trop de commandes (Managing fichier par fichier, 15+ min, echecs). Le tour n'est pas termine. L'ecran reste sur Working. Les messages Queued n'ont pas demarre.

## Que faire

1. Esc une fois.
2. Si rien : Ctrl+C dans le terminal de navin-cli.
3. Relancer `.venv/bin/navin-cli`.
4. Un seul objectif, pas tout le CRM d'un coup.

Correction produit (plafond de commandes paralleles) : seulement si tu le demandes.
