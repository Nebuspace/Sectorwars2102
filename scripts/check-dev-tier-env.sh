#!/usr/bin/env bash
# Static regression guard for the dev-tier VITE_API_URL / VITE_WS_URL pin
# (WO-QTI-DEVTIER-VITE). Renders `docker compose config` only — never starts
# a container — so it's safe to run on the Mac or any host with the Docker
# CLI. Requires `jq`.
#
# Usage: scripts/check-dev-tier-env.sh
set -euo pipefail
cd "$(dirname "$0")/.."

FAIL=0

assert_empty() {
  local label="$1" value="$2"
  if [ -n "$value" ]; then
    echo "FAIL: $label resolved to \"$value\", expected empty (same-origin)"
    FAIL=1
  else
    echo "OK:   $label is empty (same-origin)"
  fi
}

echo "--- dev tier (docker-compose.yml + docker-compose.dev.yml), poisoned host env ---"
DEV_JSON=$(API_BASE_URL=https://poison.example WS_BASE_URL=wss://poison.example \
  docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile development \
  config --format json 2>/dev/null)

assert_empty "player-client VITE_API_URL" "$(jq -r '.services["player-client"].environment.VITE_API_URL' <<<"$DEV_JSON")"
assert_empty "player-client VITE_WS_URL"  "$(jq -r '.services["player-client"].environment.VITE_WS_URL'  <<<"$DEV_JSON")"
assert_empty "admin-ui VITE_API_URL"      "$(jq -r '.services["admin-ui"].environment.VITE_API_URL'      <<<"$DEV_JSON")"
assert_empty "admin-ui VITE_WS_URL"       "$(jq -r '.services["admin-ui"].environment.VITE_WS_URL'       <<<"$DEV_JSON")"

echo ""
echo "--- dev tier WITHOUT the override file: documents the exact failure mode the pin exists for ---"
UNPINNED_API=$(API_BASE_URL=https://poison.example WS_BASE_URL=wss://poison.example \
  docker compose -f docker-compose.yml --profile development config --format json 2>/dev/null \
  | jq -r '.services["player-client"].environment.VITE_API_URL')
if [ "$UNPINNED_API" = "https://poison.example" ]; then
  echo "OK:   confirmed — omitting '-f docker-compose.dev.yml' does NOT apply the pin;"
  echo "      the dev launcher/sync script MUST pass it, every time"
else
  echo "FAIL: expected the un-pinned render to leak the poisoned host env, got \"$UNPINNED_API\""
  FAIL=1
fi

echo ""
echo "--- deployed tier (docker-compose.yml alone, --profile default): unaffected by docker-compose.dev.yml's mere existence ---"
DEFAULT_API=$(docker compose -f docker-compose.yml --profile default config --format json 2>/dev/null \
  | jq -r '.services["player-client"].environment.VITE_API_URL')
echo "INFO: default-profile player-client VITE_API_URL renders as \"$DEFAULT_API\" (host .env dependent — not itself a pass/fail signal, just proof the override file is inert unless explicitly -f'd in)"

echo ""
if [ "$FAIL" -ne 0 ]; then
  echo "check-dev-tier-env.sh: FAILED"
  exit 1
fi
echo "check-dev-tier-env.sh: PASSED"
