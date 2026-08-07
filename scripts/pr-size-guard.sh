#!/usr/bin/env bash
# pr-size-guard.sh — enforce GitHub Copilot review ceilings on a promotion slice / PR.
#
# GitHub's required `copilot_code_review` gate REFUSES a PR that exceeds EITHER
# ceiling: >300 changed files OR >20,000 changed lines. A PR that trips either is
# unmergeable (the gate can never go green). This guard blocks above those walls
# and warns in a soft band so slices keep headroom.
#
# Usage: run inside a git work tree on the slice branch:
#   pr-size-guard.sh [--target <base-ref>]     (default base: master)
# Measures the three-dot diff base...HEAD = exactly what the PR/Copilot reviews.
# Exit 0 = OK (may print WARN); exit 1 = BLOCK (over a hard ceiling).
set -euo pipefail

TARGET="master"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)   TARGET="$2"; shift 2 ;;
    --target=*) TARGET="${1#*=}"; shift ;;
    -h|--help)  grep '^#' "$0" | grep -v '^#!' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)          echo "pr-size-guard: unknown arg '$1'" >&2; shift ;;
  esac
done

# Cwd-safe: always measure from the repo root.
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "pr-size-guard: not inside a git work tree" >&2
  exit 2
}
cd "$ROOT"

# Hard ceilings (Copilot refuses above these); soft band leaves buffer.
BLOCK_FILES=300 ; WARN_FILES=200
BLOCK_LINES=20000 ; WARN_LINES=15000

# Three-dot: merge-base(TARGET,HEAD)..HEAD — what the PR actually shows Copilot.
files=$(git diff --name-only "${TARGET}...HEAD" | wc -l | tr -d ' ')
# --numstat: added<TAB>deleted<TAB>path; binaries emit "-" (awk reads as 0). Sum add+del.
lines=$(git diff --numstat "${TARGET}...HEAD" | awk '{a+=$1; d+=$2} END{printf "%d", a+d+0}')
# Two-dot: tip-to-tip novel work (stale merge-base can make three-dot a phantom).
files2=$(git diff --name-only "${TARGET}..HEAD" | wc -l | tr -d ' ')
lines2=$(git diff --numstat "${TARGET}..HEAD" | awk '{a+=$1; d+=$2} END{printf "%d", a+d+0}')

echo "pr-size-guard: three-dot (PR/Copilot) base=${TARGET} → ${files} files, ${lines} changed lines"
echo "pr-size-guard: two-dot (real work outstanding) → ${files2} files, ${lines2} changed lines"

status=0
if (( files > BLOCK_FILES )); then echo "  ❌ BLOCK: ${files} files > ${BLOCK_FILES} — Copilot refuses; re-slice smaller."; status=1; fi
if (( lines > BLOCK_LINES )); then echo "  ❌ BLOCK: ${lines} lines > ${BLOCK_LINES} — Copilot refuses; re-slice smaller."; status=1; fi
if (( status == 0 )); then
  (( files > WARN_FILES )) && echo "  ⚠️ WARN: ${files} files > ${WARN_FILES} (near the 300 ceiling)."
  (( lines > WARN_LINES )) && echo "  ⚠️ WARN: ${lines} lines > ${WARN_LINES} (near the 20k ceiling)."
  echo "  ✅ OK — within Copilot ceilings (≤${BLOCK_FILES} files, ≤${BLOCK_LINES} lines)."
fi
exit "$status"
