#!/bin/bash
# Upload release assets without `gh release upload --clobber`.
#
# The assets embedded in GET /releases/tags/{tag} can stay stale after a
# replace. --clobber deletes those ids, GitHub returns 404, and the new
# file never uploads. GET /releases/{id}/assets is the current list.
set -euo pipefail

tag="${1:?tag}"
shift
if [ "$#" -lt 1 ]; then
  echo "usage: release-upload.sh <tag> <file>..." >&2
  exit 1
fi
repo="${GITHUB_REPOSITORY:?}"

release_id="$(gh api "repos/${repo}/releases/tags/${tag}" --jq .id)"
if [ -z "${release_id}" ] || [ "${release_id}" = "null" ]; then
  echo "release ${tag} was not found" >&2
  exit 1
fi

# name, id, size. The filename is not interpolated into jq: a name like
# "foo.txt" is otherwise parsed as a jq filter.
matching_assets() {
  local base="$1"
  gh api --paginate "repos/${repo}/releases/${release_id}/assets" \
    --jq '.[] | [.name, (.id|tostring), (.size|tostring)] | @tsv' \
    | awk -F '\t' -v name="$base" '$1 == name { print $2 "\t" $3 }'
}

upload_one() {
  local file="$1"
  local base local_size attempt rows id remote count
  base="$(basename "${file}")"
  local_size="$(wc -c < "${file}" | tr -d '[:space:]')"
  attempt=1
  while [ "${attempt}" -le 5 ]; do
    rows="$(matching_assets "${base}" || true)"
    if [ -n "${rows}" ]; then
      while IFS=$'\t' read -r id _size; do
        [ -n "${id}" ] || continue
        if ! gh api --method DELETE -H "Accept: application/vnd.github+json" \
          "repos/${repo}/releases/assets/${id}" >/dev/null; then
          echo "delete ${base} ${id} failed"
        fi
      done <<< "${rows}"
    fi
    if gh release upload "${tag}" "${file}" --repo "${repo}"; then
      rows="$(matching_assets "${base}" || true)"
      count="$(printf '%s\n' "${rows}" | sed '/^$/d' | wc -l | tr -d '[:space:]')"
      remote="$(printf '%s\n' "${rows}" | awk -F '\t' 'NR==1 { print $2 }')"
      if [ "${count}" = "1" ] && [ "${remote}" = "${local_size}" ]; then
        echo "uploaded ${base} (${local_size})"
        return 0
      fi
      echo "size check failed for ${base}: remote=${remote:-empty} local=${local_size}"
    else
      echo "upload failed for ${base}, attempt ${attempt}"
    fi
    attempt=$((attempt + 1))
    sleep $((attempt * 5))
  done
  echo "giving up on ${base}" >&2
  return 1
}

for file in "$@"; do
  upload_one "${file}"
done
