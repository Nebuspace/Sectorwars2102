# WO-WARP-GATE-FACTION-ACCESS

**Seat:** Monk (dispatched by Samantha)
**Branch:** `wo/WARP-GATE-FACTION-ACCESS`
**Base:** `feat/expeditions-vista` @ `bb97b994`

## Verify-first findings (Samantha, pre-dispatch)

Two independent, pre-existing gaps this WO closes — same shape, unrelated causes:

1. **Player-client denial UX.** `GameDashboard.tsx`'s `movementResult` cockpit
   alert always rendered `cockpit-alert success` / `✅ NAVIGATION COMPLETE`,
   even when the server returned `{success: false, message: "ERR_GATE_..."}`
   for a refused gate traversal (`warp_gate_service.check_traversal_access`,
   `:1857-1934`). A denied hop looked identical to a successful one.
   Sibling alerts (`harvestResult`, `investigateResult`, the docking-full
   branch of `dockingResult`) already branch on `success`/`full` — this one
   never did.

2. **`npc_factions` grant-write gap (NP3 default-DENY).**
   `npc_movement_service._npc_gate_access_granted` (`:69-87`) already READS
   `tunnel.access_requirements["npc_factions"]` (default-DENY: an NPC
   faction may only traverse a player-built gate if the owner granted it —
   FEATURES/economy/npc-traders.md "Cross-region routing and warp gates")
   but nothing ever WROTE that key. Identical shape to the pre-existing
   `faction_rep_min`/`faction_rep_max`/`toll_bypass` gap
   `set_gate_access_layers` (WO-QUALITY-techdebt-gate-access-setters)
   already closed — this WO extends the same setter with a fourth optional
   layer instead of inventing a new endpoint.

## Scope (residual — implementation only)

1. **`services/player-client/src/components/pages/GameDashboard.tsx`** —
   movementResult alert now branches on `success === false`: `error` CSS
   class (mirrors `dockingResult`/`harvestResult`), `🚫 GATE ACCESS DENIED`
   header when the message is `ERR_GATE_`-prefixed, `⚠️ NAVIGATION REFUSED`
   for any other failure, `✅ NAVIGATION COMPLETE` unchanged on success;
   encounter log only rendered on success; `role="status"` added.
2. **`services/player-client/src/components/pages/movementAlertPresentation.ts`**
   (new) — the three pure branching decisions (CSS variant, header copy,
   show-encounters) extracted out of the JSX so they're vitest-able without
   mounting `GameDashboard`.
3. **`services/gameserver/src/services/warp_gate_service.py`** —
   `set_gate_access_layers` gains an optional `npc_factions: Optional[List[str]] = None`
   parameter (None = omit/preserve, matching the existing three layers'
   convention). New `_validate_npc_factions` validates: list, each entry a
   non-empty snake_case string (`^[a-z][a-z0-9_]*$`) ≤50 chars, list capped
   at `MAX_ACCESS_LIST_ENTRIES`, deduped preserving order. Persists under
   `access_requirements["npc_factions"]` and returns it in the result dict.
   The grant key is a **duplicated literal**, not an import, from
   `npc_movement_service._NPC_FACTION_GRANT_KEY` — importing it would create
   a `warp_gate_service -> npc_movement_service -> movement_service ->
   warp_gate_service` cycle (`movement_service` imports `warp_gate_service`
   at module scope). A dedicated unit test asserts the two constants stay
   equal.
4. **`services/gameserver/src/api/routes/warp_gates.py`** —
   `SetAccessLayersRequest`/`SetAccessLayersResponse` gain `npc_factions`;
   the existing `PATCH /warp-gates/{gate_id}/access-requirements` route
   passes it through unchanged (same owner-only, PATCH-semantics endpoint
   used for the other three layers — no new endpoint).
5. **Tests:**
   - `services/gameserver/tests/unit/test_gate_access_setters.py` — new
     `TestValidateNpcFactions` (shape/pattern/length/count validation),
     `TestNpcFactionGrantKeyMustMatch` (the drift guard for the duplicated
     constant), new `set_gate_access_layers` cases (write, empty-list
     revoke, omitted-preserves, all-four-together, reject-before-mutate),
     and a round-trip proof through
     `npc_movement_service._npc_gate_access_granted`.
   - `services/player-client/src/components/pages/movementAlertPresentation.test.ts`
     (new) — 12 cases covering all three exported helpers.

## Out of scope (deliberately not touched)

- Gatewright owner-side UI panel to configure `npc_factions` from the
  player client — a follow-up; this WO closes the write gap at the
  service/API layer only (PATCH is already reachable via the existing
  `access-requirements` endpoint, just with no dedicated UI control yet).
- No available-moves pre-filter for NPCs or players.
- No schema/migration — `npc_factions` is a JSONB key under the existing
  `access_requirements` column, identical storage shape to its three
  siblings.
- `faction_rep_min`/`faction_rep_max` semantics, `DETECT-REP`, contraband,
  and `PersonalReputation` are untouched.
- Mining (`harvestResult`) UX was already correct (`success ? ... : 'error'`)
  before this WO — not touched.

## Accept criteria

1. `movementResult.success === false` renders `cockpit-alert error` with
   `role="status"`; the header is `🚫 GATE ACCESS DENIED` for an
   `ERR_GATE_`-prefixed message and `⚠️ NAVIGATION REFUSED` otherwise; the
   encounter log is suppressed. `success !== false` (including the legacy
   no-`success`-field `leavePlanet` shape) still renders the pre-existing
   success chrome unchanged.
2. `PATCH /warp-gates/{gate_id}/access-requirements` with
   `{"npc_factions": ["terran_federation"]}` persists that list under
   `tunnel.access_requirements.npc_factions` and
   `npc_movement_service._npc_gate_access_granted(tunnel, "terran_federation")`
   returns `True` immediately after (and `False` for any other faction —
   default-DENY preserved). Omitting `npc_factions` on a call leaves an
   existing grant list unchanged; passing `[]` explicitly revokes every
   grant.
3. Invalid `npc_factions` (non-list, non-string/empty entry, over-length
   entry, non-snake_case entry, over-`MAX_ACCESS_LIST_ENTRIES` list) is
   rejected with a 400 *before* the gate row is locked or the tunnel's
   JSONB is mutated (same discipline as the three existing layers).
4. `warp_gate_service._NPC_FACTION_GRANT_KEY == npc_movement_service._NPC_FACTION_GRANT_KEY`
   is pinned by a unit test (drift guard for the duplicated literal).

## Proof

- `poetry run pytest tests/unit/test_gate_access_setters.py -q` — 49 passed
  (23 test methods before this WO; 39 after — +16 new/extended methods,
  several parametrized, hence 49 collected cases).
- `poetry run pytest tests/unit -k "warp_gate or npc_movement or npc_gate" -q`
  — 84 passed (no regressions in the surrounding suite).
- `poetry run ruff check` on the two touched gameserver files — no new
  findings introduced by this WO (2 pre-existing import-sort nits in
  files this WO touched were auto-fixed as a drive-by; all other findings
  — `C901`/`B904` — are pre-existing, in functions this WO does not touch).
- `npx vitest run src/components/pages/movementAlertPresentation.test.ts`
  — 12 passed.
- `npx vitest run src/components/pages/__tests__/GameDashboard` — 59 passed
  (no regressions across the 11 existing GameDashboard test files).
- `npx tsc --noEmit` — clean.
- live-prove: n/a (no live TWGS-equivalent surface for this repo; proof is
  the unit/vitest suites above plus a static read of the traversal/route
  wiring).
