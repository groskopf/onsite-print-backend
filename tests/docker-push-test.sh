#!/bin/bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
temp="$(mktemp -d)"
trap 'rm -f -- "$temp/docker" "$temp/calls" "$temp/output" "$temp/docker-push.sh" "$temp/.env"; rmdir -- "$temp"' EXIT
cp "$root/docker-push.sh" "$temp/docker-push.sh"
printf '%s\n' IMAGE_TAG=1.0.0 > "$temp/.env"

cat > "$temp/docker" <<'MOCK'
#!/bin/bash
set -euo pipefail
printf '%s|%s|%s\n' "$*" "${IMAGE_TAG:-}" "$PWD" >> "$CALLS"
if [[ "$1" == login ]]; then
  token="$(cat)"
  [[ "$token" == test-secret ]]
fi
if [[ "${FAIL_STEP:-}" == "$1 ${2:-}" ]]; then
  echo "Simulated Docker failure" >&2
  exit 42
fi
MOCK
chmod +x "$temp/docker"
export PATH="$temp:$PATH"
export CALLS="$temp/calls"
export CR_PAT=test-secret
export GHCR_USERNAME=test-user
unset FAIL_STEP

expect_failure() {
  : > "$CALLS"
  if bash "$temp/docker-push.sh" "$@" > "$temp/output" 2>&1; then
    echo "Expected publishing to fail: $*" >&2
    exit 1
  fi
  if grep -q 'Published\|test-secret' "$temp/output"; then
    echo "Failure output leaked a token or reported success" >&2
    exit 1
  fi
}

expect_failure 1.0.0
[[ ! -s "$CALLS" ]]
rm -- "$temp/.env"
expect_failure
[[ ! -s "$CALLS" ]]
for setting in '' 'IMAGE_TAG=' 'IMAGE_TAG=latest' 'IMAGE_TAG=1.0' 'IMAGE_TAG=01.0.0' 'IMAGE_TAG=1.0.0-rc1' 'IMAGE_TAG=invalid/tag' 'IMAGE_TAG=1.0.0 extra' 'UNRELATED=1.0.0' $'IMAGE_TAG=1.0.0\nEXTRA=value'; do
  printf '%s\n' "$setting" > "$temp/.env"
  expect_failure
  [[ ! -s "$CALLS" ]]
done
printf '%s\n' IMAGE_TAG=1.0.0 > "$temp/.env"
unset CR_PAT
expect_failure
[[ ! -s "$CALLS" ]]
export CR_PAT=test-secret

for step in 'login ghcr.io' 'compose build' 'compose push'; do
  export FAIL_STEP="$step"
  expect_failure
  [[ "$(tail -n 1 "$CALLS")" == "$step"* ]]
done
unset FAIL_STEP

: > "$CALLS"
export IMAGE_TAG=ignored-shell-tag
(cd / && bash "$temp/docker-push.sh") > "$temp/output" 2>&1
expected="$(printf '%s\n' \
  "login ghcr.io -u test-user --password-stdin|1.0.0|$temp" \
  "compose build --pull backend|1.0.0|$temp" \
  "compose push backend|1.0.0|$temp")"
[[ "$(cat "$CALLS")" == "$expected" ]]
grep -q 'Published ghcr.io/groskopf/onsite-print-backend:1.0.0' "$temp/output"
! grep -q test-secret "$temp/output"

unset GHCR_USERNAME
printf '%s\n' IMAGE_TAG=2.0.0 > "$temp/.env"
bash "$temp/docker-push.sh" > "$temp/output" 2>&1
grep -q 'login ghcr.io -u groskopf --password-stdin|2.0.0|' "$CALLS"

for versions in '1.2.9 1.2.10' '1.2.99 1.2.100' '1.2.99999999999999999999 1.2.100000000000000000000'; do
  read -r current next <<< "$versions"
  printf 'IMAGE_TAG=%s\n' "$current" > "$temp/.env"
  bash "$temp/docker-push.sh" > "$temp/output" 2>&1
  [[ "$(cat "$temp/.env")" == "IMAGE_TAG=$next" ]]
done

echo "Publishing script tests passed"
