#!/usr/bin/env bash
# hub-live-prove-check.sh — Post the `live-prove` required status on a PR head.
#
# ORCHESTRATOR-ONLY. Do NOT run this from a workflow (that would defeat the gate).
# This script is documentation + a runnable helper for the hub seat.
#
# The `live-prove` check is a required status check on the `feat/new-feature-development` (and master) ruleset.
# Only the orchestrator may mark it, and only after actually running live tests
# (or confirming an explicit docs/CI-infra n/a). No workflow may auto-green it.
#
# Prerequisites:
#   gh auth status
#   Prefer a GitHub App / token with checks:write (Check Runs API).
#   User PATs often get HTTP 403 on Check Runs — this script falls back to the
#   Commit Statuses API (context=live-prove) so the merge ritual still works.
#
# Usage:
#   ./scripts/hub-live-prove-check.sh <HEAD_SHA> success "hosts: anet, rogue · N of M passed"
#   ./scripts/hub-live-prove-check.sh <HEAD_SHA> n/a "CI-infra: no live-login product path"
#   ./scripts/hub-live-prove-check.sh <HEAD_SHA> failure "live-login failed: <brief reason>"
#
# Argument 3 is the SUMMARY text. No secrets — hosts and result counts only.
#
# Hub merge ritual (ordered): suite green → this script → gh pr merge →
#   ./scripts/hub-wo-merge-cleanup.sh wo/<ID> [worktree…]
#
# Diversity gate (Max 2026-07-26): product PRs need multi-server /
# multi-character evidence in SUMMARY — ≥3 catalog hosts, ≥1 NEW + ≥1
# RETURNING — e.g.:
#   hosts: a_net_online, gone_rogue, microblaster_network · NEW:2 RETURNING:3
# Single-host-only is not a valid success summary for login/ensure/play PRs.
# Docs/CI-infra may use n/a with an explicit reason.

set -euo pipefail

REPO="Nebuspace/Sectorwars2102"
CHECK_NAME="live-prove"

HEAD_SHA="${1:-}"
RESULT="${2:-}"   # "success" | "failure" | "n/a"
SUMMARY="${3:-}"

if [[ -z "$HEAD_SHA" || -z "$RESULT" || -z "$SUMMARY" ]]; then
  echo "Usage: $0 <head-sha> <success|failure|n/a> <summary-text>"
  echo ""
  echo "  success  — live tests ran and passed on orchestrator laptop"
  echo "  failure  — live tests ran and failed"
  echo "  n/a      — docs/CI-infra PR; live tests deliberately skipped (state reason in summary)"
  exit 1
fi

if [[ "$RESULT" == "n/a" ]]; then
  CONCLUSION="success"
  STATE="success"
  TITLE="Orchestrator live prove — n/a"
elif [[ "$RESULT" == "success" ]]; then
  CONCLUSION="success"
  STATE="success"
  TITLE="Orchestrator laptop live prove — passed"
elif [[ "$RESULT" == "failure" ]]; then
  CONCLUSION="failure"
  STATE="failure"
  TITLE="Orchestrator laptop live prove — FAILED"
else
  echo "ERROR: result must be 'success', 'failure', or 'n/a'. Got: $RESULT"
  exit 1
fi

echo "Posting live-prove to $REPO @ $HEAD_SHA"
echo "  result     : $RESULT"
echo "  title      : $TITLE"
echo "  summary    : $SUMMARY"
echo ""

# Prefer Check Runs (ruleset-native). Fall back to Commit Statuses on 403.
set +e
CHECK_OUT="$(
  gh api "repos/${REPO}/check-runs" \
    -f "name=${CHECK_NAME}" \
    -f "head_sha=${HEAD_SHA}" \
    -f "status=completed" \
    -f "conclusion=${CONCLUSION}" \
    -f "output[title]=${TITLE}" \
    -f "output[summary]=${SUMMARY}" \
    --jq '.id' 2>&1
)"
CHECK_RC=$?
set -e

if [[ $CHECK_RC -eq 0 ]]; then
  echo "Done — Check Run id=${CHECK_OUT}"
else
  echo "Check Runs API failed (rc=$CHECK_RC): $CHECK_OUT"
  echo "Falling back to Commit Status context=${CHECK_NAME} …"
  # Statuses API description max length is 140 — longer values → HTTP 422.
  DESC="${TITLE}: ${SUMMARY}"
  if ((${#DESC} > 140)); then
    DESC="${DESC:0:137}..."
  fi
  gh api "repos/${REPO}/statuses/${HEAD_SHA}" \
    -f "state=${STATE}" \
    -f "context=${CHECK_NAME}" \
    -f "description=${DESC}" \
    --jq '"status id=" + (.id|tostring) + " state=" + .state'
  echo "Done — Commit Status posted (wire a GitHub App for Check Runs when ready)."
fi

echo ""
echo "Next: gh pr checks <PR>  # verify live-prove green, then merge, then hub-wo-merge-cleanup.sh"
