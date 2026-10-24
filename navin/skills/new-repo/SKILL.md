---
name: new-repo
description: "Publish a local project to a new private repository on GitHub, GitLab or Forgejo: inspect first, prepare a safe first commit (.gitignore, no secrets), create the repo with forge action=repo_create, add origin and push through the git tool. Use for /publish, 'push this project to GitHub', 'put this on GitLab', 'crée un repo pour ce projet'. Never creates a second repo, never changes an existing remote, never deletes a repo."
metadata: {"navin":{"emoji":"📦","category":"devops","requires":{"bins":["git"]}}}
---

# New repository (publish a project)

Turn a local folder into a repository on the user's forge (GitHub by default,
GitLab or Forgejo on request) and push it, without surprises.

Tools: `git` (status, init, add, commit, remote, push; it uses Navin's credential
bridge, so no token ever appears in a URL or the chat), `forge` action
`repo_create` (asks the user for approval, refused in unattended runs), and
`request_credentials` (kind=git_host) when the host is not connected yet.

## Hard limits

- Never delete a repository, local or remote.
- Never change an existing remote (`origin` or any other). Never `git remote set-url`.
- Never create a second repository for a project that already has a remote.
- Private by default. Public only when the user said so (`/publish --public`, "public repo").
- Never commit anything that looks like a secret without asking first.
- Never ask the user to paste a token or password in the chat.

## 1. Inspect first

1. `git rev-parse --is-inside-work-tree` (git action=status works too).
2. `git remote -v` (git action=remote).
3. If a remote already exists: say which one (host and owner/repo, no credentials),
   offer to commit and push the pending work there, and **stop**. Do not create
   anything new, do not touch the remote.

## 2. Prepare

1. List what would be committed (git action=status; untracked files included).
   Show it grouped and short, not a raw dump.
2. Make sure `.gitignore` covers, for the stack you see:
   - dependency dirs: `node_modules/`, `.venv/`, `venv/`, `vendor/`, `target/`, `__pycache__/`
   - build outputs: `dist/`, `build/`, `.next/`, `out/`, `coverage/`, `*.egg-info/`
   - secrets: `.env`, `.env.*` (keep `!.env.example`), `*.pem`, `*.key`, `id_rsa*`,
     `credentials*.json`, `*.p12`, `.navin/secrets*`, `.navin/credentials*`
   - OS / editor noise: `.DS_Store`, `Thumbs.db`, `.idea/`, `.vscode/` (unless the project shares settings)
   Add the missing lines; do not rewrite the user's existing rules.
3. Anything that still looks like a secret (a `.env` already tracked, a key file,
   a config with `password=` / `token=`): name the file and ask before including it.
4. Only if needed: `git init -b main` (git action=init) and one initial commit
   ("Initial commit") staging the named paths you listed (git action=add with paths),
   never a blind add-everything over secrets. If commits already exist, keep them.

## 3. Create and push

1. Name: the folder name in lowercase with dashes (`My App` -> `my-app`), or the
   name the user gave. If the forge says the name is taken (`forge_repo_exists`),
   try one close variant (`my-app-1` or `my-app-<year>`); if that is taken too, ask.
2. `forge action=repo_create name=<name> host=<host> private=true` (add `org=` only
   when the user named an organization or group). The user approves it in a card.
3. If the host is not connected, call `request_credentials(kind=git_host, host=<host>)`
   once with a clear reason (GitHub offers the device sign-in). Then retry once.
4. `git action=remote create=true name=origin remote=<clone_url>` (only because no
   remote existed in step 1).
5. `git action=push set_upstream=true` (pushes the current branch, `main` by default).

## 4. Report

Say in one or two lines: the repository page (`web_url`), private or public, the
branch pushed and how many files went in. Mention anything you left out on purpose
(a secret file, a large binary).

## Plain-language variant (novice users)

When the user does not speak git, keep the same steps but talk about outcomes:

- "Your project is not online yet. I will put it in a private space on GitHub that
  only you can see." (instead of "init, commit, create remote, push")
- "I found a file with passwords (`.env`). I will keep it off the internet."
- "GitHub needs to know it is you: a sign-in window opens, approve it there."
- At the end: "Done. Your project is here: <link>. It is private."

Never show raw command output to a novice unless they ask; summarize it.
