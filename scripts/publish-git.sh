#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Deux destinations, deux arbres :
#
#   Forgejo  forgejo  branche prod-v2  = dépôt privé complet
#            https://forgejo.navinspire.ai/Navinspire/navin-claw
#
#   GitHub   origin  branche main  = CLI public (sans site / desktop / AWS publish)
#            https://github.com/navinspire-ai/navin-agi
#   GitHub   github  branche main  = ancien dépôt public filtré
#            https://github.com/navinspire-ai/navin
#
# Usage :
#   scripts/publish-git.sh forgejo              # git push forgejo prod
#   scripts/publish-git.sh github --dry-run     # montre le diff, ne pousse pas
#   scripts/publish-git.sh github               # overlay depuis main + push
#   scripts/publish-git.sh github --from=prod   # overlay depuis une autre ref
#   scripts/publish-git.sh remotes              # ajoute / affiche les remotes
#
# Le push GitHub ne réécrit pas l'historique Forgejo. Il part de origin/main
# (navin-agi), y dépose l'arbre filtré, et ajoute un commit.
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FORGEJO_URL="https://forgejo.navinspire.ai/Navinspire/navin-claw.git"
GITHUB_AGI_URL="https://github.com/navinspire-ai/navin-agi.git"
GITHUB_URL="https://github.com/navinspire-ai/navin.git"
EXCLUDE_FILE="$ROOT/scripts/git-public-exclude.txt"
PRESERVE_FILE="$ROOT/scripts/git-public-preserve.txt"
WORKTREE="$ROOT/.worktrees/github-main"

die() { echo "publish-git: $*" >&2; exit 1; }

trim_comment() {
  local raw="$1"
  local trimmed="${raw%%#*}"
  trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"
  trimmed="${trimmed#"${trimmed%%[![:space:]]*}"}"
  printf '%s' "$trimmed"
}

usage() {
  sed -n '12,16p' "$0" | sed 's/^# \?//'
  exit 2
}

remote_for_url() {
  local want="$1"
  local name url
  while read -r name url; do
    if [ "$url" = "$want" ]; then
      printf '%s' "$name"
      return 0
    fi
  done < <(git remote -v | awk '/\(push\)$/ {print $1, $2}')
  return 1
}

ensure_remote() {
  local name="$1"
  local url="$2"
  if git remote get-url "$name" >/dev/null 2>&1; then
    local current
    current="$(git remote get-url "$name")"
    if [ "$current" != "$url" ]; then
      echo "publish-git: remote $name = $current (attendu $url)" >&2
    fi
    return 0
  fi
  if remote_for_url "$url" >/dev/null; then
    return 0
  fi
  git remote add "$name" "$url"
  echo "publish-git: remote $name ajouté ($url)"
}

ensure_remotes() {
  git config core.hooksPath .githooks
  ensure_remote forgejo "$FORGEJO_URL"
  ensure_remote origin "$GITHUB_AGI_URL"
  ensure_remote github "$GITHUB_URL"
}

show_remotes() {
  ensure_remotes
  echo "forgejo (Forgejo / prod-v2) : $(git remote get-url forgejo 2>/dev/null || remote_for_url "$FORGEJO_URL")"
  echo "origin  (GitHub / main AGI) : $(git remote get-url origin 2>/dev/null || true)"
  echo "github  (GitHub / ancien)   : $(git remote get-url github 2>/dev/null || true)"
  echo
  echo "Pousser le privé :  make publish-forgejo"
  echo "Pousser le public : make publish-github-dry   puis   make publish-github"
}

is_excluded() {
  local rel="$1"
  case "$rel" in
    *:Zone.Identifier) return 0 ;;
  esac
  local pat trimmed
  while IFS= read -r pat || [ -n "$pat" ]; do
    trimmed="$(trim_comment "$pat")"
    [ -z "$trimmed" ] && continue
    case "$rel" in
      "$trimmed"|"${trimmed%/}"|"${trimmed%/}"/*) return 0 ;;
    esac
  done < "$EXCLUDE_FILE"
  return 1
}

read_preserve_paths() {
  local pat trimmed
  while IFS= read -r pat || [ -n "$pat" ]; do
    trimmed="$(trim_comment "$pat")"
    [ -z "$trimmed" ] && continue
    printf '%s\n' "$trimmed"
  done < "$PRESERVE_FILE"
}

publish_forgejo() {
  ensure_remotes
  local remote
  remote="$(remote_for_url "$FORGEJO_URL")" || die "aucun remote Forgejo ($FORGEJO_URL)"
  local branch
  branch="$(git rev-parse --abbrev-ref HEAD)"
  if [ "$branch" != "prod" ]; then
    echo "publish-git: branche courante = $branch (on pousse quand même refs/heads/prod)."
  fi
  git push "$remote" prod:prod
}

wipe_worktree_files() {
  local dir="$1"
  local entry
  shopt -s dotglob nullglob
  for entry in "$dir"/* "$dir"/.[!.]* "$dir"/..?*; do
    [ -e "$entry" ] || continue
    case "$(basename "$entry")" in
      .git) continue ;;
    esac
    rm -rf "$entry"
  done
  shopt -u dotglob nullglob
}

remove_excluded_from() {
  local dest="$1"
  local trimmed
  while IFS= read -r pat || [ -n "$pat" ]; do
    trimmed="$(trim_comment "$pat")"
    [ -z "$trimmed" ] && continue
    rm -rf "$dest/${trimmed%/}"
  done < "$EXCLUDE_FILE"
  find "$dest" -name '*:Zone.Identifier' -delete 2>/dev/null || true
}

restore_preserved() {
  local snapshot="$1"
  local dest="$2"
  local rel parent
  while IFS= read -r rel; do
    [ -e "$snapshot/$rel" ] || continue
    parent="$(dirname "$rel")"
    mkdir -p "$dest/$parent"
    rm -rf "$dest/$rel"
    cp -a "$snapshot/$rel" "$dest/$rel"
  done < <(read_preserve_paths)
}

overlay_workdir_onto() {
  local dest="$1"
  local src_ref="$2"
  local head
  head="$(git rev-parse --abbrev-ref HEAD)"
  if [ "$src_ref" != "HEAD" ] && [ "$src_ref" != "$head" ]; then
    return 0
  fi
  local rel parent
  while IFS= read -r rel; do
    [ -n "$rel" ] || continue
    [ -e "$ROOT/$rel" ] || continue
    parent="$(dirname "$rel")"
    mkdir -p "$dest/$parent"
    rm -rf "$dest/$rel"
    cp -a "$ROOT/$rel" "$dest/$rel"
  done < <(
    git diff --name-only --diff-filter=ACMR HEAD
    git ls-files --others --exclude-standard
  )
}

restore_gitlinks() {
  local dest="$1"
  local src_sha="$2"
  local line mode sha path
  while read -r mode sha path; do
    [ "$mode" = "160000" ] || continue
    rm -rf "$dest/$path"
    mkdir -p "$dest/$(dirname "$path")"
    git -C "$dest" update-index --add --cacheinfo "160000,$sha,$path"
  done < <(git -C "$ROOT" ls-tree -r "$src_sha" | awk '$1=="160000" {print $1, $3, $4}')
}

assert_no_private_leak() {
  local dest="$1"
  local leaked=""
  [ -e "$dest/site" ] && leaked="${leaked}site "
  [ -e "$dest/src" ] && leaked="${leaked}src "
  [ -e "$dest/desktop" ] && leaked="${leaked}desktop "
  [ -e "$dest/desktop-electron" ] && leaked="${leaked}desktop-electron "
  [ -e "$dest/os" ] && leaked="${leaked}os "
  [ -e "$dest/templates" ] && leaked="${leaked}templates "
  [ -e "$dest/tmp-preview-fixture" ] && leaked="${leaked}tmp-preview-fixture "
  [ -e "$dest/packaging/aws" ] && leaked="${leaked}packaging/aws "
  [ -e "$dest/navin/license_client.py" ] && leaked="${leaked}license_client "
  [ -e "$dest/navin/license_sync.py" ] && leaked="${leaked}license_sync "
  [ -e "$dest/navin/webui/account_api.py" ] && leaked="${leaked}account_api "
  [ -e "$dest/scripts/publish-os-to-s3.sh" ] && leaked="${leaked}publish-os-to-s3.sh "
  [ -e "$dest/scripts/publish-templates-to-s3.sh" ] && leaked="${leaked}publish-templates-to-s3.sh "
  [ -e "$dest/scripts/publish-media-templates-to-s3.sh" ] && leaked="${leaked}publish-media-templates-to-s3.sh "
  if [ -n "$leaked" ]; then
    die "filtre incomplet, traces privées encore présentes : $leaked"
  fi
}

ensure_github_worktree() {
  local remote
  remote="$(remote_for_url "$GITHUB_AGI_URL")" || die "aucun remote navin-agi ($GITHUB_AGI_URL)"
  mkdir -p "$(dirname "$WORKTREE")"
  if git worktree list --porcelain | grep -q "^worktree ${WORKTREE}$"; then
    git -C "$WORKTREE" fetch "$remote" main
    git -C "$WORKTREE" checkout --detach "$remote/main"
    git -C "$WORKTREE" reset --hard "$remote/main"
  elif [ -d "$WORKTREE" ]; then
    die "dossier $WORKTREE présent mais pas un worktree. Supprime-le puis relance."
  else
    git fetch "$remote" main
    git worktree add --detach "$WORKTREE" "$remote/main"
  fi
}

publish_github() {
  local dry=0
  local src_ref="main"
  local arg
  for arg in "$@"; do
    case "$arg" in
      --dry-run) dry=1 ;;
      --from=*) src_ref="${arg#--from=}" ;;
      --from)
        die "usage : --from=<ref> (ex. --from=prod)"
        ;;
      *) die "option inconnue pour github : $arg (attendu --dry-run et/ou --from=<ref>)" ;;
    esac
  done

  [ -f "$EXCLUDE_FILE" ] || die "manque $EXCLUDE_FILE"
  [ -f "$PRESERVE_FILE" ] || die "manque $PRESERVE_FILE"

  ensure_remotes

  git rev-parse --verify "$src_ref" >/dev/null 2>&1 || die "ref inconnue : $src_ref"
  local src_sha
  src_sha="$(git rev-parse "$src_ref")"

  echo "publish-git: source $src_ref ($src_sha)"
  echo "publish-git: fetch origin/main (navin-agi)..."
  git fetch origin main

  ensure_github_worktree

  local snapshot
  snapshot="$(mktemp -d "${TMPDIR:-/tmp}/navin-github-preserve.XXXXXX")"
  trap 'rm -rf "$snapshot"' RETURN

  while IFS= read -r rel; do
    if [ -e "$WORKTREE/$rel" ]; then
      mkdir -p "$snapshot/$(dirname "$rel")"
      cp -a "$WORKTREE/$rel" "$snapshot/$rel"
    fi
  done < <(read_preserve_paths)

  wipe_worktree_files "$WORKTREE"
  git archive "$src_sha" | tar -x -C "$WORKTREE"
  overlay_workdir_onto "$WORKTREE" "$src_ref"
  remove_excluded_from "$WORKTREE"
  restore_preserved "$snapshot" "$WORKTREE"
  assert_no_private_leak "$WORKTREE"

  git -C "$WORKTREE" add -A
  restore_gitlinks "$WORKTREE" "$src_sha"

  if git -C "$WORKTREE" diff --cached --quiet; then
    echo "publish-git: GitHub/main est déjà à jour (rien à committer)."
    return 0
  fi

  echo "publish-git: overlay $(git -C "$WORKTREE" diff --cached --shortstat)"
  if [ "$dry" -eq 1 ]; then
    echo "publish-git: dry-run, pas de commit ni de push."
    git -C "$WORKTREE" diff --cached --stat | tail -n 50
    echo
    echo "Fichiers privés absents de l'index (contrôle) :"
    git -C "$WORKTREE" ls-files | grep -E '^(site/|src/|desktop/|desktop-electron/|os/|templates/|tmp-preview-fixture/|packaging/aws/|navin/license_client\.py|navin/license_sync\.py|navin/webui/account_api\.py|scripts/publish-.*-s3\.sh)' && die "fuite" || echo "  aucun (site / desktop / os / AWS / navin.live)"
    git -C "$WORKTREE" reset --hard HEAD >/dev/null
    return 0
  fi

  git -C "$WORKTREE" commit -m "$(cat <<EOF
Sync public CLI tree from ${src_ref} ${src_sha:0:10}

Site, desktop, os, navin.live and AWS publish stay on Forgejo only.
EOF
)"
  local remote
  remote="$(remote_for_url "$GITHUB_AGI_URL")" || die "aucun remote navin-agi"
  echo "publish-git: push $remote HEAD:main"
  NAVIN_PUBLIC_PUSH=1 git -C "$WORKTREE" push "$remote" HEAD:main
}

cmd="${1:-}"
shift || true
case "$cmd" in
  remotes|remote) show_remotes ;;
  forgejo|prod) publish_forgejo ;;
  github|main) publish_github "$@" ;;
  -h|--help|help|"") usage ;;
  *) die "commande inconnue '$cmd' (forgejo | github | remotes)" ;;
esac
