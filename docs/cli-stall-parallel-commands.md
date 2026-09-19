# Blocage CLI : trop de commandes en parallele

Date : 2026-09-18
Contexte : TUI lancee via `.venv/bin/navin-cli` (wrapper vers `navin.cli.commands.run_cli`). Pas Cursor, pas un crash Windows, pas Azure Trusted Signing.

## Ce qui se voit

- Compteur du type : Edited 790 files, ran 123 commands, 16 failed, 15+ minutes.
- Lignes `Managing path/to/file.py` : fichiers encore ouverts par l'agent.
- `Queued 2 after current reply` : les messages suivants (CRM, front) n'ont pas demarre.
- Esc to interrupt : le tour n'est pas termine.

## Cause

Ce n'est pas le terminal Windows qui plante. C'est un tour d'agent qui lance trop d'appels (git, lint, manage fichier par fichier) sans attendre la fin. Les timeouts s'empilent, les echecs ne stoppent pas la boucle, le working tree reste sale, et rien d'autre ne part tant que la reponse n'est pas close.

Hors sujet : `AZURE_CLIENT_ID` / `packaging/windows/signing.env` (Trusted Signing).

## Si l'ecran Navin CLI ne repond plus

Le binaire `.venv/bin/navin-cli` n'est qu'un shebang Python. L'UI figee est le TUI Textual (`navin/tui/app.py`) : file `_queued_prompts`, lignes Working / Queued, Esc = interrupt du tour en cours.

1. Esc une fois. Attendre que Working disparaisse.
2. Si rien ne bouge : Ctrl+C dans le terminal qui a lance `.venv/bin/navin-cli`.
3. Si le terminal reste mort : fermer l'onglet / tuer le process Python de navin-cli, relancer `.venv/bin/navin-cli` dans le projet cible.
4. `git status` dans ce projet. Ne pas committer un tour de 790 fichiers.
5. Les prompts Queued (CRM / front) ne partent qu'apres la fin du tour. Un objectif a la fois.

Rien a reinstaller. Hors sujet : `AZURE_CLIENT_ID` / `packaging/windows/signing.env`.

## Produit Navin (si on corrige ici)

Pistes, non implementees dans ce document :

- Plafonner les appels paralleles (exec / manage) par tour.
- Arreter la boucle apres N echecs exec, au lieu de relancer.
- Afficher clairement "tour bloque, interrupt" au lieu d'une file infinie `Managing`.
