#!/usr/bin/env bash
# pr-size-guard.sh — enforce GitHub Copilot review ceilings on a promotion slice / PR.
#
# GitHub's required `copilot_code_review` gate REFUSES a PR that exceeds EITHER
# ceiling: >300 changed files OR >20,000 changed lines. A PR that trips either is
# unmergeable (the gate can never go green). This guard blocks above those walls
# and warns in a soft band so slices keep headroom.
#
# Usage: run inside a git work tree on the slice branch:
#   pr-size-guard.sh [--target <base-ref>] [--head <ref>]
#     --target  base branch (default: master). Bare names resolve via origin/<name>.
#     --head    tip ref to measure (default: auto). Explicit refs pass through.
#               When omitted: if the current branch tracks a remote and local HEAD
#               is behind that upstream, best-effort fetch + measure the upstream
#               (with a WARN) so a stale checkout cannot silently understate;
#               ahead-only WIP keeps measuring local HEAD.
# Measures the three-dot diff base...HEAD_REF = exactly what the PR/Copilot reviews.
# Bare branch names (e.g. master) resolve via origin/<name> after a best-effort
# fetch, so a stale local <name> tip cannot inflate/deflate the gauge.
# Exit 0 = OK (may print WARN); exit 1 = BLOCK (over a hard ceiling).
set -euo pipefail

TARGET="master"
HEAD_ARG=""   # empty = auto-resolve (prefer upstream when available)
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)   TARGET="$2"; shift 2 ;;
    --target=*) TARGET="${1#*=}"; shift ;;
    --head)     HEAD_ARG="$2"; shift 2 ;;
    --head=*)   HEAD_ARG="${1#*=}"; shift ;;
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

# Resolve base: bare names → origin/<name> after fetch (stale local tip is the
# failure mode this WO exists to close). Explicit refs (origin/foo, SHA, …) pass through.
if [[ "$TARGET" == */* ]] || [[ "$TARGET" =~ ^[0-9a-fA-F]{7,40}$ ]]; then
  BASE_REF="$TARGET"
else
  # Best-effort refresh of origin/<TARGET>; keep going offline with whatever we have.
  git fetch --quiet origin "+refs/heads/${TARGET}:refs/remotes/origin/${TARGET}" 2>/dev/null || true
  if git rev-parse --verify --quiet "refs/remotes/origin/${TARGET}" >/dev/null; then
    BASE_REF="origin/${TARGET}"
  else
    BASE_REF="$TARGET"
    echo "pr-size-guard: WARN — origin/${TARGET} unavailable; using local ${TARGET} (may be stale)" >&2
  fi
fi

git rev-parse --verify --quiet "${BASE_REF}^{commit}" >/dev/null || {
  echo "pr-size-guard: base ref '${BASE_REF}' not found" >&2
  exit 2
}

# Resolve tip (HEAD side): mirror the base-side stale-local guard.
# Explicit --head always wins. Otherwise prefer origin/<current-branch> when the
# branch tracks a remote, so a lagging checkout cannot silently understate.
if [[ -n "$HEAD_ARG" ]]; then
  # Symbolic / already-qualified / SHA tips pass through. Bare branch names
  # resolve via origin/<name> (same pattern as --target). Never rewrite HEAD/@.
  if [[ "$HEAD_ARG" == "HEAD" || "$HEAD_ARG" == "@" || "$HEAD_ARG" == */* || "$HEAD_ARG" =~ ^[0-9a-fA-F]{7,40}$ ]]; then
    HEAD_REF="$HEAD_ARG"
  else
    git fetch --quiet origin "+refs/heads/${HEAD_ARG}:refs/remotes/origin/${HEAD_ARG}" 2>/dev/null || true
    if git rev-parse --verify --quiet "refs/remotes/origin/${HEAD_ARG}" >/dev/null; then
      HEAD_REF="origin/${HEAD_ARG}"
    else
      HEAD_REF="$HEAD_ARG"
      echo "pr-size-guard: WARN — origin/${HEAD_ARG} unavailable; using local ${HEAD_ARG} (may be stale)" >&2
    fi
  fi
else
  HEAD_REF="HEAD"
  BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  if [[ -n "$BRANCH" && "$BRANCH" != "HEAD" ]]; then
    UPSTREAM="$(git rev-parse --abbrev-ref "${BRANCH}@{upstream}" 2>/dev/null || true)"
    if [[ -n "$UPSTREAM" ]]; then
      # Best-effort refresh of the tracked remote branch.
      REMOTE="${UPSTREAM%%/*}"
      RBRANCH="${UPSTREAM#*/}"
      git fetch --quiet "$REMOTE" "+refs/heads/${RBRANCH}:refs/remotes/${UPSTREAM}" 2>/dev/null || true
      if git rev-parse --verify --quiet "refs/remotes/${UPSTREAM}" >/dev/null; then
        LOCAL_SHA="$(git rev-parse HEAD)"
        REMOTE_SHA="$(git rev-parse "refs/remotes/${UPSTREAM}")"
        if [[ "$LOCAL_SHA" != "$REMOTE_SHA" ]]; then
          BEHIND="$(git rev-list --count "${LOCAL_SHA}..${REMOTE_SHA}" 2>/dev/null || echo 0)"
          AHEAD="$(git rev-list --count "${REMOTE_SHA}..${LOCAL_SHA}" 2>/dev/null || echo 0)"
          # Stale-behind is the silent-understatement bug: prefer the remote tip.
          # Ahead-only = unpushed WIP — keep measuring local HEAD (the PR tip).
          # Diverged = WARN; still prefer remote so a lagging fork of a shared
          # branch can't understate (override with --head HEAD if needed).
          if [[ "$BEHIND" != "0" ]]; then
            echo "pr-size-guard: WARN — local HEAD (${LOCAL_SHA:0:8}) behind ${UPSTREAM} (${REMOTE_SHA:0:8}) by ${BEHIND} (ahead=${AHEAD}) — measuring ${UPSTREAM} to avoid stale understatement" >&2
            HEAD_REF="$UPSTREAM"
          fi
        fi
      else
        echo "pr-size-guard: WARN — ${UPSTREAM} unavailable; measuring local HEAD (may be stale)" >&2
      fi
    fi
  fi
fi

git rev-parse --verify --quiet "${HEAD_REF}^{commit}" >/dev/null || {
  echo "pr-size-guard: head ref '${HEAD_REF}' not found" >&2
  exit 2
}

MERGE_BASE="$(git merge-base "${BASE_REF}" "${HEAD_REF}")"
echo "pr-size-guard: merge-base=${MERGE_BASE} base=${BASE_REF} head=${HEAD_REF} (target=${TARGET})"

# Three-dot: merge-base(BASE,HEAD)..HEAD — what the PR actually shows Copilot.
files=$(git diff --name-only "${BASE_REF}...${HEAD_REF}" | wc -l | tr -d ' ')
# --numstat: added<TAB>deleted<TAB>path; binaries emit "-" (awk reads as 0). Sum add+del.
lines=$(git diff --numstat "${BASE_REF}...${HEAD_REF}" | awk '{a+=$1; d+=$2} END{printf "%d", a+d+0}')
# Two-dot: tip-to-tip novel work (cross-check against three-dot phantoms).
files2=$(git diff --name-only "${BASE_REF}..${HEAD_REF}" | wc -l | tr -d ' ')
lines2=$(git diff --numstat "${BASE_REF}..${HEAD_REF}" | awk '{a+=$1; d+=$2} END{printf "%d", a+d+0}')

echo "pr-size-guard: three-dot (PR/Copilot) base=${BASE_REF} → ${files} files, ${lines} changed lines"
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
