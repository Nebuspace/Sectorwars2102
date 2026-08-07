# WO-ADM-FACTION-DIPLOMACY-CITE-HONESTY

**Seat:** `impl-sectorwars-cursor`  
**Branch:** `wo/ADM-FACTION-DIPLOMACY-CITE-HONESTY`  
**Base:** `feat/expeditions-vista` @ `37cb4494`

## Goal

Repair admin-ui comment honesty: `FactionManagement.tsx` cited `models/faction.py:95` for the diplomacy 3-value scale (`hostile` / `neutral` / `friendly`). Line 95 is blank; the scale comment lives on `diplomacy_stance` at `:106`.

## Scope

- `services/admin-ui/src/components/pages/FactionManagement.tsx` — cite `:95` → `:106`

## Out of bounds

- No behavior / type / API changes
- Stay clear of K2/K2b/contraband/quantum/slipdrive/hangar/tow
- Do not touch gameserver runtime

## Accept

1. Comment cites `models/faction.py:106`
2. Diff is comment-only (plus this WO)

## Proof

- `diplomacy_stance` at `faction.py:106` carries `# hostile, neutral, friendly`
- live-prove: n/a (comment honesty)
