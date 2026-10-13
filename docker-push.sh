#!/bin/bash
set +x
set -euo pipefail

if [[ $# -ne 0 ]]; then
  echo "Usage: bash docker-push.sh (set the version in .env)" >&2
  exit 1
fi

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ ! -r .env ]]; then
  echo "Cannot read image version from .env" >&2
  exit 1
fi
version_setting="$(cat .env)"
if [[ ! "$version_setting" =~ ^IMAGE_TAG=((0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*))$ ]]; then
  echo ".env must contain only IMAGE_TAG=<major.minor.patch> without leading zeros" >&2
  exit 1
fi
export IMAGE_TAG="${BASH_REMATCH[1]}"
prefix="${IMAGE_TAG%.*}"
patch="${IMAGE_TAG##*.}"
next_patch=""
carry=1
for ((i=${#patch}-1; i>=0; i--)); do
  digit=$(( ${patch:i:1} + carry ))
  next_patch="$((digit % 10))$next_patch"
  carry=$((digit / 10))
done
if ((carry)); then next_patch="1$next_patch"; fi
next_tag="$prefix.$next_patch"
if ((${#next_tag} > 128)); then
  echo "Next image tag exceeds the 128-character limit" >&2
  exit 1
fi

if [[ -z "${CR_PAT:-}" ]]; then
  echo "CR_PAT environment variable is not set" >&2
  exit 1
fi

export GHCR_USERNAME="${GHCR_USERNAME:-groskopf}"

printf '%s' "$CR_PAT" | docker login ghcr.io -u "$GHCR_USERNAME" --password-stdin
docker compose build --pull backend
docker compose push backend

echo "Published ghcr.io/groskopf/onsite-print-backend:$IMAGE_TAG"
printf 'IMAGE_TAG=%s\n' "$next_tag" > .env
echo "Next version in .env: $next_tag"
