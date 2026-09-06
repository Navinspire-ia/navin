#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Génère, une fois pour toutes, la clé qui signe les mises à jour de Navin.
#
# Usage :   bash scripts/update-keygen.sh
#
# Écrit dans .env :
#   UPDATE_SIGNING_KEY  clé privée Ed25519 (base64, 32 octets) - SECRET
#   UPDATE_BASE_URL     racine HTTPS où le manifeste signé est publié
#
# La clé publique correspondante est dérivée à chaque build et embarquée dans
# l'application : c'est elle qui décide quelles mises à jour sont acceptées.
#
# ATTENTION - cette clé est la racine de confiance de la mise à jour :
#   * la perdre  = plus aucune version future ne pourra s'installer sur les
#                  copies déjà distribuées (il faudra réinstaller à la main) ;
#   * la fuiter  = n'importe qui peut faire installer son binaire à vos
#                  utilisateurs. Elle ne doit jamais être commitée (.env est
#                  déjà dans .gitignore) ni sortir d'un coffre à secrets.
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ROOT}/.env"
PYTHON="${PYTHON:-python3}"
if [[ -x "${ROOT}/.venv/bin/python" ]]; then
  PYTHON="${ROOT}/.venv/bin/python"
fi

DEFAULT_BUCKET="${BUCKET_NAME:-navinagent}"
DEFAULT_REGION="${AWS_DEFAULT_REGION:-eu-north-1}"
DEFAULT_URL="https://${DEFAULT_BUCKET}.s3.${DEFAULT_REGION}.amazonaws.com"

# The deriver writes to a path, and /dev/stdout is not openable everywhere
# (containers, redirected shells), which would abort this script after the key
# had already been written - the worst possible moment to stop.
PUBLIC_FILE="$(mktemp)"
trap 'rm -f "$PUBLIC_FILE"' EXIT

derive_public_key() {
  UPDATE_SIGNING_KEY="$1" "$PYTHON" \
    "${ROOT}/packaging/update/sign_manifest.py" \
    --public-key-only --write-public-key "$PUBLIC_FILE" >/dev/null
  cat "$PUBLIC_FILE"
}

touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

if grep -q '^[[:space:]]*UPDATE_SIGNING_KEY[[:space:]]*=[[:space:]]*[^[:space:]]' "$ENV_FILE"; then
  echo "UPDATE_SIGNING_KEY existe déjà dans .env : rien à faire."
  echo
  echo "La remplacer casserait la mise à jour de toutes les copies déjà"
  echo "distribuées, qui n'accepteraient plus les manifestes signés avec la"
  echo "nouvelle clé. Pour la remplacer sciemment, supprimer la ligne d'abord."
  PUBLIC=$(derive_public_key "$(sed -n 's/^[[:space:]]*UPDATE_SIGNING_KEY[[:space:]]*=[[:space:]]*//p' \
    "$ENV_FILE" | tail -n 1 | tr -d '\042\047\015')")
  echo
  echo "Clé publique correspondante : ${PUBLIC}"
  exit 0
fi

echo "Génération d'une clé de signature Ed25519..."
PRIVATE=$("$PYTHON" - <<'PY'
import base64
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

key = Ed25519PrivateKey.generate()
raw = key.private_bytes(
    serialization.Encoding.Raw,
    serialization.PrivateFormat.Raw,
    serialization.NoEncryption(),
)
print(base64.b64encode(raw).decode())
PY
)

{
  echo ""
  echo "# Mises à jour signées (scripts/update-keygen.sh). NE JAMAIS COMMITER."
  echo "UPDATE_SIGNING_KEY=${PRIVATE}"
} >> "$ENV_FILE"

if ! grep -q '^[[:space:]]*UPDATE_BASE_URL[[:space:]]*=[[:space:]]*[^[:space:]]' "$ENV_FILE"; then
  echo "UPDATE_BASE_URL=${DEFAULT_URL}" >> "$ENV_FILE"
fi

PUBLIC=$(derive_public_key "$PRIVATE")

cat <<EOF

Clé écrite dans .env (permissions 600, déjà ignoré par git).
  UPDATE_BASE_URL : $(sed -n 's/^[[:space:]]*UPDATE_BASE_URL[[:space:]]*=[[:space:]]*//p' "$ENV_FILE" | tail -n 1)
  Clé publique    : ${PUBLIC}

Sauvegardez la clé privée dans votre coffre à secrets MAINTENANT : sans elle,
aucune version future ne pourra s'installer sur les copies déjà distribuées.

Étapes suivantes :
  1. Reconstruire (make appimage-release / desktop-exe / desktop-dmg) : la clé
     publique est alors embarquée dans l'application.
  2. Publier (make aws-upload) : le manifeste signé part sur S3.
  3. Ouvrir s3:GetObject sur arn:aws:s3:::${DEFAULT_BUCKET}/stable/* dans la
     politique du bucket, sinon l'application reçoit un 403 et se taira.
EOF
