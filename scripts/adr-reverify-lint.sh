#!/usr/bin/env bash
# adr-reverify-lint.sh — flag Folded / Distributed-fold Index rows missing or
# stale _(re-verified YYYY-MM-DD)_ tags (ADR/README.md "Re-verification cadence").
#
# Auth: WO-INFRA-ADR-REVERIFY-CADENCE-LINT
#
# Usage (from any cwd; defaults to sibling sw2102-docs next to this repo):
#   scripts/adr-reverify-lint.sh
#   scripts/adr-reverify-lint.sh --docs-root /path/to/sw2102-docs
#   scripts/adr-reverify-lint.sh --stale-days 30 --strict
#
# Exit 0 = clean (may print WARN for Active Distributed-fold rows without a tag
#           unless --strict). Exit 1 = MISSING tag on a Fully-folded row, and/or
#           STALE tag older than --stale-days when --fail-stale is set.
# Exit 2 = usage / path errors.
set -euo pipefail

STALE_DAYS=30
STRICT=0
FAIL_STALE=0
DOCS_ROOT="${SW2102_DOCS_ROOT:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --docs-root)   DOCS_ROOT="$2"; shift 2 ;;
    --docs-root=*) DOCS_ROOT="${1#*=}"; shift ;;
    --stale-days)  STALE_DAYS="$2"; shift 2 ;;
    --stale-days=*) STALE_DAYS="${1#*=}"; shift ;;
    --strict)      STRICT=1; shift ;;
    --fail-stale)  FAIL_STALE=1; shift ;;
    -h|--help)
      grep '^#' "$0" | grep -v '^#!' | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "adr-reverify-lint: unknown arg '$1'" >&2; exit 2 ;;
  esac
done

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "adr-reverify-lint: not inside a git work tree" >&2
  exit 2
}

if [[ -z "$DOCS_ROOT" ]]; then
  # Sibling layout: …/Nebuspace/Sectorwars2102 + …/Nebuspace/sw2102-docs
  CANDIDATE="$(cd "$ROOT/.." && pwd)/sw2102-docs"
  if [[ -d "$CANDIDATE/ADR" ]]; then
    DOCS_ROOT="$CANDIDATE"
  else
    echo "adr-reverify-lint: cannot find sw2102-docs (set --docs-root or SW2102_DOCS_ROOT)" >&2
    exit 2
  fi
fi

README="$DOCS_ROOT/ADR/README.md"
if [[ ! -f "$README" ]]; then
  echo "adr-reverify-lint: missing $README" >&2
  exit 2
fi

# Python body: parse Index tables, report missing/stale tags.
# shellcheck disable=SC2016
python3 - "$README" "$STALE_DAYS" "$STRICT" "$FAIL_STALE" <<'PY'
from __future__ import annotations

import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

readme = Path(sys.argv[1])
stale_days = int(sys.argv[2])
strict = sys.argv[3] == "1"
fail_stale = sys.argv[4] == "1"

text = readme.read_text(encoding="utf-8")

# Section awareness: Active vs Fully folded (historical).
sections: list[tuple[str, str]] = []
parts = re.split(r"(?=^### )", text, flags=re.M)
for part in parts:
    first = part.splitlines()[0] if part.strip() else ""
    if first.startswith("### Active"):
        sections.append(("active", part))
    elif first.startswith("### Fully folded"):
        sections.append(("historical", part))

row_re = re.compile(
    r"^\|\s*(\d{4})\s*\|\s*\[[^\]]+\]\([^)]+\)\s*\|\s*(.+?)\s*\|?\s*$",
    re.M,
)
tag_re = re.compile(r"_\(re-verified\s+(\d{4}-\d{2}-\d{2})\)_")
fold_re = re.compile(r"\b(Folded|Distributed-fold)\b", re.I)

today = datetime.now(timezone.utc).date()
missing_hist: list[str] = []
missing_active: list[str] = []
stale: list[tuple[str, int, str]] = []
ok = 0
scanned = 0

for kind, body in sections:
    for m in row_re.finditer(body):
        num, status = m.group(1), m.group(2).strip()
        if not fold_re.search(status):
            continue
        scanned += 1
        tag = tag_re.search(status)
        if not tag:
            if kind == "historical":
                missing_hist.append(num)
            else:
                missing_active.append(num)
            continue
        d = datetime.strptime(tag.group(1), "%Y-%m-%d").date()
        age = (today - d).days
        if age > stale_days:
            stale.append((num, age, tag.group(1)))
        else:
            ok += 1

print(f"adr-reverify-lint: scanned {scanned} Folded/Distributed-fold Index rows in {readme}")
print(f"  ok (tag ≤{stale_days}d): {ok}")
if missing_hist:
    print(f"  MISSING (Fully-folded historical): {', '.join(missing_hist)}")
if missing_active:
    label = "MISSING" if strict else "WARN"
    print(f"  {label} (Active / open Distributed-fold, no tag): {', '.join(missing_active)}")
if stale:
    print(
        "  STALE (>"
        + str(stale_days)
        + "d): "
        + ", ".join(f"{n} ({age}d, last {when})" for n, age, when in stale)
    )

rc = 0
if missing_hist:
    rc = 1
if strict and missing_active:
    rc = 1
if fail_stale and stale:
    rc = 1
if rc == 0 and not missing_hist and not (strict and missing_active) and not (fail_stale and stale):
    print("  result: OK")
else:
    print(f"  result: FAIL (exit {rc})" if rc else "  result: OK (warnings only)")
sys.exit(rc)
PY
