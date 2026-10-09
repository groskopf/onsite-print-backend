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
if [[ ! "$version_setting" =~ ^IMAGE_TAG=([a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127})$ ]]; then
  echo ".env must contain only IMAGE_TAG=<valid container image tag>" >&2
  exit 1
fi
export IMAGE_TAG="${BASH_REMATCH[1]}"

if [[ -z "${CR_PAT:-}" ]]; then
  echo "CR_PAT environment variable is not set" >&2
  exit 1
fi

export GHCR_USERNAME="${GHCR_USERNAME:-groskopf}"

printf '%s' "$CR_PAT" | docker login ghcr.io -u "$GHCR_USERNAME" --password-stdin
docker compose build --pull backend
docker compose push backend

echo "Published ghcr.io/groskopf/onsite-print-backend:$IMAGE_TAG"
