# NEON Roadmap — run tracking

Source of the original nine: `.claude/admin-ui-master-list.md` → "SUGGESTED BATCHES (NEON-sized)"
(8 numbered batches; #8 contained two separate runs). Status updated 2026-06-12 after run 12.
This file is the living tracker — update at every run's N10.

## The original nine (admin-UI master list batches)

| # | Run | Scope | Status |
|---|-----|-------|--------|
| 1 | **Stop the bleeding** | /teams crash, port editor 500s, route shadowing, MFA/audit async, ErrorBoundary | ✅ DONE — runs 5–6 (`a888a54`, `df399ef`) |
| 2 | **Users & Teams admin work** | real users CRUD, team admin actions | ✅ DONE — run 5 (users rewire) + run 6 (teams honest-disable; implement-for-real still open, see backlog) |
| 3 | **Honest lists** | station hardcodes → real fields, pagination, sector locations | ✅ DONE — run 6 section A (`5147620`, `d6ee03e`) |
| 4 | **Security console** | P0.8/P0.9/P3.4 + remaining auth gaps: real security overview page | ⬜ NOT RUN |
| 5 | **Fleet & drone command** | surface working fleet/drone admin | 🚧 PARTIAL — FleetHealthReport rescued (run 6); drone command + fleet admin surface still unbuilt |
| 6 | **Governor retheme + presentation pass** | P4.1–4.6 display-sensibility batch | ⬜ NOT RUN |
| 7 | **Dead code purge + typecheck gate** | P5.* purge + tsc gate in CI | 🚧 PARTIAL — purge done (runs 6 + 12, ~8,500 lines); **tsc gate not wired** (vite builds don't typecheck; ~78 pre-existing tsc errors ship silently) |
| 8 | **Economy levers** (own run) | P2.2 admin economy control panel | ⬜ NOT RUN — needs Max design decisions first |
| 9 | **Scopes** (own run) | P2.1 AdminScopeGrant per ADR-0058 | ⬜ NOT RUN — needs Max decisions first |

## Accumulated backlog (parking lots, runs 6–12) — candidates for future runs

### Gameplay-meaty
- **Faction mission completion** — accept works; completion mechanics await `faction-mission-completion` pending decision (sw2102-docs DECISIONS.md)
- **Siege semantics** — three pending decisions filed (`siege-turn-length`, `siege-vulnerability-vs-assault`, terraforming tick blessing); vulnerability is display-only until decided
- **Port assault** — fully coded, deliberately disabled (`player_combat.py` "not yet authorized"); Max scoped military takeover as deferred — needs explicit go-ahead + Station defense scalars
- **NPC respawn loop** — spawn is one-shot CLI; `respawn_eligible_at` set but no Loop B (population healthy today, will drain eventually)
- **Pirate hulls** — Captains ship placeholder LIGHT_FREIGHTER stats (canon gap — needs hull table from Max)
- **Genesis tiers** — price-only theater (success_rate/process_hours never persisted or applied)
- **Premium station classes** — Class-8 +20% / Class-9 +25% multipliers documented (trading.md 🚧) but unapplied
- **Gate-material sourcing** — `gate-material-sourcing` pending decision (WJ 200 cargo can't haul phase materials)
- **Warp gate phase 2 extras** — tolls, access modes, transfer/sale, destruction & salvage, upgrades, get-home tow ritual (all design-only per canon)
- **Quantum supply chain** — shard harvesting + crystal assembly (canon design-only; columns exist)

### Cockpit / player UI
- **COMMS v2** — manual recipient entry, inbox pagination, sent view (needs backend endpoint), message delete UI
- **Sector-type worldgen variety** — dev galaxy has only STANDARD+NEBULA; BLACK_HOLE/VOID/STAR_CLUSTER/ASTEROID_FIELD viewscreen variants code-complete but unproven (worldgen/BANG change; relates to duplicate-Earths invariant)
- **Page-level spinner branches** — PlanetManager/TeamManager (4 sites each) still remount their own subtrees on their local loading states
- **Orbitron font** — referenced across CRT surfaces, never loaded (Max call: load it or standardize Courier New)
- **CORRIDORS IN SECTOR** — arrival-side gates not listed; admin gate-management surface unbuilt
- **Construction claim ignores custom ship_name**
- **Beacon-phase cancel** exists in API, no UI affordance
- **PLANETARY monitor** intermittently empty on first load (transient)

### Hygiene / infrastructure
- **tsc gate** (the missing half of batch 7) — wire `tsc --noEmit` into builds/CI; burn down the ~78 pre-existing errors
- **Dual refresh-token locks** (AuthContext + apiClient) uncoordinated — unify
- **Route-collision startup check** (master list C.5) — shadowing is a recurring bug class
- **update_port blind-setattr whitelist** (admin P0.2 remnant)
- **process_terraforming_tick** zero callers — scheduler-or-delete decision
- **/quantum/minimap** caching + GameContext-level minimap state (refetch per remount)
- **team_service treasury** — commodity-to-cargo routing design (currently honestly rejected)
- **foundation-sprint e2e dir** fuller audit (one stale spec may remain)
- **Sector sub-50-spacing approach gap** — jump bands can't fine-approach; canon texture, maybe intentional

## How to use
Say `neon` and the run self-selects from this file + fresh discovery. Say `neon <batch name>` to direct a run at a specific row. Rows needing Max decisions are marked — they cannot self-select.
