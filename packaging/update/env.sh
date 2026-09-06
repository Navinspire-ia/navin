# Update signing material, resolved the same way by every build script.
# Sourced, not executed: it exports into the caller's environment.
#
# The values come from .env, next to the AWS credentials, rather than from an
# exported variable the release operator has to remember. A forgotten export
# does not fail the build: it produces an application that looks perfectly
# normal and can never update itself, and the defect only surfaces one version
# later, on machines nobody can reach any more.

# Read one key=value out of .env. The two values are a base64 key and a URL,
# so a plain read is enough; quotes and CR (edited from Windows) are stripped.
navin_env_value() {
  [ -f "$1/.env" ] || return 0
  sed -n "s/^[[:space:]]*$2[[:space:]]*=[[:space:]]*//p" "$1/.env" \
    | tail -n 1 | tr -d '\042\047\015'
}

# Write (or deliberately remove) the two files the frozen app reads at runtime:
# navin/update/public_key.txt and navin/update/default_base_url.txt.
navin_embed_update_config() {
  navin_root="$1"
  navin_py="$2"
  UPDATE_SIGNING_KEY="${UPDATE_SIGNING_KEY:-$(navin_env_value "$navin_root" UPDATE_SIGNING_KEY)}"
  UPDATE_BASE_URL="${UPDATE_BASE_URL:-$(navin_env_value "$navin_root" UPDATE_BASE_URL)}"
  export UPDATE_SIGNING_KEY UPDATE_BASE_URL

  navin_key_file="$navin_root/navin/update/public_key.txt"
  navin_url_file="$navin_root/navin/update/default_base_url.txt"

  if [ -n "${UPDATE_SIGNING_KEY:-}" ]; then
    UPDATE_SIGNING_KEY="$UPDATE_SIGNING_KEY" "$navin_py" \
      "$navin_root/packaging/update/sign_manifest.py" \
      --public-key-only \
      --write-public-key "$navin_key_file"
  else
    # A key left in the checkout by an earlier build would be embedded here and
    # would reject every manifest signed with the current one.
    rm -f "$navin_key_file"
  fi
  if [ -n "${UPDATE_BASE_URL:-}" ]; then
    printf '%s\n' "${UPDATE_BASE_URL%/}" > "$navin_url_file"
  else
    rm -f "$navin_url_file"
  fi

  if [ -z "${UPDATE_SIGNING_KEY:-}" ] || [ -z "${UPDATE_BASE_URL:-}" ]; then
    printf '\n' >&2
    printf 'ATTENTION : build SANS mise a jour automatique.\n' >&2
    printf '  UPDATE_SIGNING_KEY et UPDATE_BASE_URL sont absents de .env.\n' >&2
    printf '  Cette version pourra etre installee, mais jamais se mettre a\n' >&2
    printf '  jour toute seule. Corriger avant publication :\n' >&2
    printf '    bash scripts/update-keygen.sh\n\n' >&2
  else
    printf 'Mise a jour automatique : active (%s).\n' "$UPDATE_BASE_URL" >&2
  fi
}
