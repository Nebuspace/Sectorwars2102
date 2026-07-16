# Intra-system position model — design for ratification

**Status:** ✅ RATIFIED (Max 2026-07-16) — building  
**Author:** impl-sectorwars · 2026-07-16T04:47:46Z  
**Ratification:** burn cost **(a) Free**; empty-space Travel To **in v1**; remaining knobs owned by Implementer (fixed 1440×335 reference aspect; active NPCs fly legs, SLEEP park; build now — Orchestrator offline).  
**Why now:** Windshield turn/burn/flip/brake is live but **client-cosmetic**. Reload loses pose; two players in the same sector do not share NPC/player trajectories. Max confirmed the target: server+DB authority, reload-stable, multiplayer-synced.

**Canon anchors (read first):**
- [`SYSTEMS/sector-presence.md`](../../../sw2102-docs/SYSTEMS/sector-presence.md) — who-is-**in-sector**; no x/y today
- [`SYSTEMS/realtime-bus.md`](../../../sw2102-docs/SYSTEMS/realtime-bus.md) — REST mutates, WS push-only; sector rooms
- [`OPERATIONS/realtime.md`](../../../sw2102-docs/OPERATIONS/realtime.md) — multiplayer ops view
- [`FEATURES/gameplay/movement.md`](../../../sw2102-docs/FEATURES/gameplay/movement.md) — sector↔sector only
- [`DECISIONS.md`](../../../sw2102-docs/DECISIONS.md) — `intrasystem-burn-cost` + `system-scan-mechanics` still ⏳ Pending
- Flight profile timings already shipped in client: `TRAVEL_*` / `OTHER_FLIGHT_*` (orient 1.0s → move 6.4s → settle 0.8s)

---

## 1. Problem

| Concern | Today | Target |
|---------|-------|--------|
| Player in-system pose | Client-only (`WindshieldTableau` / flight context) | Server-authoritative, DB-persisted |
| NPC in-system pose | Client seed + local clock (`otherShipFlightPose`) | Same authority as players |
| Reload | Resting-anchor reset | Same x/y/heading/phase |
| Two clients, same sector | Divergent cosmetics | Shared timeline (leg plans) |
| Dock/Land proximity | Client-gated | Server-validated from pose |

Sector hops (`Player.current_sector_id`, `NPCCharacter.current_sector_id`, `players_present`) stay as they are. This design adds **pose inside the sector**.

---

## 2. Design principles (non-negotiable)

1. **REST commits, WS notifies** — no WS-only mutation ([ADR-0094](../../../sw2102-docs/ADR/0094-api-first-playability.md)).
2. **Broadcast leg plans, not frames** — clients interpolate with the shared flight profile from `leg_started_at` + server time. Avoids per-tick pose spam on the bus.
3. **One profile for everyone** — player hull and NPC contacts use the same timing constants (already mirrored as `OTHER_FLIGHT_*` ≈ `TRAVEL_*`).
4. **%-space is the wire/DB unit** — matches the windshield band (`x_pct`/`y_pct` in `[0,100]`, `heading_deg` CSS-style). Celestial layout stays generate-once; pose is relative to that painted band, not AU physics.
5. **Sector presence stays the membership ledger** — pose is *denormalized into* presence entries for sector UIs, but authoritative columns live on `Player` / `NPCCharacter`.

---

## 3. Proposed rulings (Max — pick / amend)

### R1 · `intrasystem-burn-cost` (closes pending DECISION)

**Recommend: (a) Free** — burns cost wall-clock transit time only; **0 turns**. Turns remain a sector-hop resource.

- Keeps turn economy untouched
- Matches “instant sector hop, animated in-system” feel already shipping
- Dock/Land/salvage stay gated by **pose proximity**, not turn spend

Reject (b)/(c) unless Max wants positioning to tax the turn pool.

### R2 · Coordinate + state model

Authoritative fields (player + NPC):

| Field | Type | Meaning |
|-------|------|---------|
| `pose_x_pct` | Numeric(6,3) | Band X % |
| `pose_y_pct` | Numeric(6,3) | Band Y % |
| `pose_heading_deg` | Numeric(8,2) | Continuous heading (may exceed ±360 for spin direction) |
| `pose_phase` | Enum | `idle` \| `orienting` \| `accelerating` \| `gliding` \| `brake_turn` \| `braking` \| `final_orient` \| `halt_turn` \| `halt_brake` |
| `pose_burning` | Bool | Exhaust on |
| `leg_from_x_pct` / `leg_from_y_pct` | Numeric | Leg origin (nullable when idle) |
| `leg_to_x_pct` / `leg_to_y_pct` | Numeric | Leg destination |
| `leg_target_kind` | String? | `planet` \| `station` \| `point` \| `null` |
| `leg_target_id` | String? | Planet/station UUID when applicable |
| `leg_started_at` | timestamptz? | Server UTC start of current phase timeline |
| `leg_profile_ms` | JSONB? | Optional override; default = shared constants |

**Idle parked:** `pose_phase=idle`, leg fields null, x/y/heading = last settled pose.

**Mid-leg derived position:** pure function of  
`(leg_from, leg_to, leg_started_at, now, profile_ms)` — same math clients already use. DB may store last-committed phase endpoints; optional periodic materialize for queries.

### R3 · Sync strategy

```
Client A commits burn ──REST──► Server
                                 ├─ write pose/leg columns
                                 ├─ mirror into Sector.players_present[i]
                                 └─ WS sector room: intrasystem.leg_started { ship_id, plan… }

Client A / B / C                 ◄── animate from plan + server_time
                                 (no per-frame WS)

On reload / join sector          GET player + sector contents
                                 → if mid-leg, seek into animation at (now - leg_started_at)
```

**Server time:** every `connected` / leg event includes `server_time` (already partially on `connected`). Clients offset local clock once; drift re-sync on sector join.

### R4 · NPC traffic

NPC Loop A (or a dedicated intrasystem tick, ~1–2 Hz wall) **schedules legs** the same way players do:

- Waypoints = real planet/station positions from `SectorCelestial` / contents layout (server must expose or recompute the same %-anchors the windshield uses — see §5 layout parity)
- Activity biases dwell (PATROL short, SLEEP long) — already sketched client-side
- On each leg commit: write NPC pose columns + presence mirror + `intrasystem.leg_started`

Sector hops still clear/reset pose (spawn at resting anchor or undock emergence point).

### R5 · Dock / Land / approach gating (server)

Move proximity checks server-side:

- `POST .../dock` / `land` / salvage: require `pose_phase in (idle, final_orient)` **and** distance(pose, host) ≤ `DOCK_RANGE` (em→% converted with band aspect, or store range in %-space)
- APPROACH from SOLAR row = `POST /helm/intrasystem/burn` with `target_kind=planet|station`

Client keeps optimistic UI; 4xx reconciles from server pose.

### R6 · Scan (ties to pending `system-scan-mechanics`)

**Out of scope for v1 pose** except: hidden wrecks/signals stay presence-flagged; reveal rules unchanged until that decision lands. Pose model must not assume scan costs.

---

## 4. Schema (additive migration)

```text
players:
  + pose_x_pct, pose_y_pct, pose_heading_deg
  + pose_phase (enum/text), pose_burning
  + leg_from_x_pct, leg_from_y_pct, leg_to_x_pct, leg_to_y_pct
  + leg_target_kind, leg_target_id
  + leg_started_at

npc_characters:  (same pose/leg columns)

-- players_present entry gains mirrored keys (enrich on write):
-- pose_x_pct, pose_y_pct, pose_heading_deg, pose_phase, pose_burning,
-- leg_*, leg_started_at
```

On sector hop / dock / land:
- Docked/landed: freeze pose at host approach point (or null pose + `is_docked`/`is_landed` remains source of truth for mode)
- Undock / lift-off: seed pose at host %-position (matches current “emerge at host” UX)

**Recommendation:** while `is_docked` or `is_landed`, pose columns still store the host anchor (so undock is trivial) but flight UI is suppressed as today.

---

## 5. Layout parity (critical)

%-positions for planets/stations must be **identical** on server and client.

Today layout math lives only in `windshieldTableauLayout.ts` (seeded from celestial composition).

**v1 approach:** port a minimal `intrasystem_layout.py` that mirrors `bodyPosition` / `stationPosition` / `starAnchor` / `safeOrbitRadii` given:
- `SectorCelestial.composition`
- fixed reference band geometry (e.g. flight band 1440×335 or aspect 0.232) baked as server constant for pose math

**Acceptance:** golden vectors — same sectorId + composition → same %-points within 0.05% on TS vs Python fixtures.

Without this, two clients could agree with each other (same TS) while the server docks at a different host — broken.

---

## 6. API surface (REST)

All under `/api/v1/helm/intrasystem/` (name bikeshed-ok):

| Method | Path | Effect |
|--------|------|--------|
| `GET` | `/pose` | Self pose + derived mid-leg sample at `server_time` |
| `POST` | `/burn` | Body: `{ target_kind, target_id }` **or** `{ x_pct, y_pct }` — commits a new leg from **derived current pose** |
| `POST` | `/halt` | Abort mid-leg → halt-turn/brake profile; persist stop point |
| `POST` | `/arrive` | Optional explicit settle (or auto on timer via server tick) |

Also fold pose into existing:
- `GET /player/me` (or current player payload)
- `GET /sectors/{id}` / `/contents` presence enrichment

**Idempotency:** `burn` while already in flight = mid-course redirect (same rules as client redirect-turn), not a freeze.

**Rate limit:** e.g. 2 burns/sec soft; halt always allowed.

---

## 7. Bus events (add to `realtime-bus.md` taxonomy)

| Type | Room | Payload |
|------|------|---------|
| `intrasystem.leg_started` | `sector:{id}` | `ship_id, is_npc, from, to, target_*, leg_started_at, profile_ms, heading_prograde, server_time` |
| `intrasystem.leg_halted` | `sector:{id}` | `ship_id, stop, heading, leg_started_at, server_time` |
| `intrasystem.leg_arrived` | `sector:{id}` | `ship_id, at, heading, server_time` |
| `intrasystem.pose_snapshot` | `sector:{id}` (on join / resync) | `ships: [{ ship_id, pose… }]` — optional batch |

Priority: below combat/trade, above chat ([`OPERATIONS/realtime.md`](../../../sw2102-docs/OPERATIONS/realtime.md) §4).

---

## 8. Client changes (after server)

1. Replace local-only `travelTo` / `otherShipFlightPose` clock with **plan store** keyed by `ship_id`.
2. Self: optimistic burn → REST → reconcile on ack / bus echo.
3. Others/NPCs: ignore local seed clock; render from presence + bus plans.
4. On mount: hydrate from `/pose` + sector presence; seek mid-leg.
5. Keep CSS/RCS/burn visuals; drive `--hdg` / left/top from shared interpolator module (extract from layout).

---

## 9. Work order decomposition (build after ratification)

### WO-ISP-0 — Canon + decisions
- Ratify R1–R6; flip `intrasystem-burn-cost` in `DECISIONS.md`
- Draft `SYSTEMS/intrasystem-movement.md` + bus taxonomy rows
- **Owner:** orchestrator after Max sign-off · **human-gated** docs push

### WO-ISP-1 — Layout parity + schema  `[gameserver]`
- Python port of %-layout + golden TS↔Py fixtures
- Alembic additive columns on `players` + `npc_characters`
- Presence mirror helpers
- **Proof:** fixture parity; migration upgrade head

### WO-ISP-2 — REST helm + player hydrate  `[gameserver]`
- `/helm/intrasystem/*` + me/sector enrichment
- Server-side proximity for dock/land (feature-flag ok)
- **Proof:** pytest burn→arrive→reload pose identical; two sequential burns redirect

### WO-ISP-3 — Bus fan-out + NPC leg scheduler  `[gameserver]`
- Emit leg_started/halted/arrived
- NPC tick schedules legs from celestial docks
- **Proof:** WS capture two subscribers receive identical plans; NPC pose survives GS restart mid-leg

### WO-ISP-4 — Client cutover  `[player-client]`
- Shared interpolator; kill cosmetic-only NPC/player clocks for flight mode
- Hydrate on reload; sector-room listeners
- **Proof:** Playwright — burn, reload, assert pose within ε; two browser contexts same NPC plan

### WO-ISP-5 — Deploy window + live prove
- Hub-mediated GS restart (migration)
- Heimdall: two sessions side-by-side visual + API ground truth

**Parallelism:** ISP-1 layout fixtures ∥ schema draft; ISP-2 after ISP-1; ISP-3 ∥ ISP-4 after ISP-2 contract frozen; ISP-5 last.

---

## 10. Explicit non-goals (v1)

- AU/physics simulation or collision
- Combat maneuvering from pose
- Changing sector↔sector turn costs
- WS mutation channel
- Perfect bit-identical pixels across different band aspect ratios (pose is %-space; each client maps to its band; docking uses %-distance)

---

## 11. Open questions for Max (ratify inline)

1. **Burn cost:** confirm **(a) Free**?
2. **Free-point travel** (context-menu Travel To empty space): allow in v1, or planet/station targets only?
3. **Band reference:** lock server layout to fixed 1440×335 aspect, or store `band_aspect` per session? (Recommend **fixed reference aspect** for authority.)
4. **NPC density:** every NPC in sector flies legs, or only those with `activity ∈ {PATROL, COMMUTE, WORK_STATION}` and sleepers stay parked?
5. **Priority vs other WIP:** build now after ratify, or park behind current playtest polish?

---

## 12. One-line ticket (if Max says go with defaults)

`Goal: authoritative in-system pose (DB+REST+leg-plan WS) for players+NPCs · paths: gameserver pose/helm/layout + player-client flight cutover · proof: reload pose stable + two clients share NPC leg_started plans.`
