-- =============================================================================
-- Ship resistance backfill -- WO-SHIP-RESIST-BACKFILL
-- =============================================================================
--
-- WHAT: One-time UPDATE copying each pre-existing ship's shield_resistance /
--   armor_rating from its hull's ship_specifications row, for ships still
--   sitting at the column default 0.0/0.0.
--
-- WHY: cc77eff (WO-SB-CR2) closed the last of four Ship-creation paths
--   (first_login_service.py, matching ship_service.py:105-106's pattern) to
--   copy spec resistance values on CREATE. Every ship created BEFORE that
--   deploy still carries 0.0/0.0 and fights unmitigated in
--   combat_service._apply_weapon_damage (_resistance_fraction, combat_service.py
--   ~:2139/:2162) alongside identical new hulls that DO mitigate -- a
--   two-class combat population that persists indefinitely without this
--   backfill. Ruling: Max ruling #6 (2026-07-10), approving the backfill
--   shape the addendum already recommended (audit/tranche-2026-07-02-
--   addendum.md:877; audit/enrichment-2026-07-05/new-wos-fresh-residuals.md:
--   113-123, WO-SHIP-RESIST-BACKFILL). The per-hull magnitude table itself
--   was ALREADY ratified separately (sw2102-docs/DECISIONS.md:99
--   "hull-combat-mitigation-table", Max 2026-06-22) -- this script only
--   executes the backfill; it does not touch or re-propose those magnitudes.
--
-- PRECONDITIONS:
--   * Run against the target database (interstitch) inside a posted
--     DEPLOY-WINDOW -- this mutates live ships rows.
--   * ship_specifications must already be seeded (src/core/
--     ship_specifications_seeder.py runs at gameserver boot; every canon
--     hull type has exactly one row -- ship_specifications.type is UNIQUE,
--     src/models/ship.py:350 -- so the join below cannot fan out).
--   * Idempotent, safe to run twice: the WHERE guard only ever matches ships
--     still AT the un-backfilled 0.0/0.0 state, so a second run affects 0
--     rows once the first run has completed.
--   * Derives, never overwrites: a ship with ANY non-zero shield_resistance
--     OR armor_rating (a deliberate custom value, or already backfilled) is
--     excluded -- BOTH columns must read exactly 0.0 for a row to be touched
--     ("rows where both values are exactly 0.0 only", per the WO).
--
-- ROW-COUNT EXPECTATIONS: on heimdall's fresh DB (no legacy ships rows) this
--   is a 0-row no-op by construction -- nothing to backfill, nothing runs
--   until the interstitch deploy window. On interstitch (has legacy player
--   ships), expect one row per ship created before the WO-SB-CR2 deploy that
--   hasn't already been custom-tuned; run the pre-count query below first to
--   see the number before committing.
--
-- PRE-RUN COUNT (run first, outside the transaction, to see what will move):
--   SELECT count(*) FROM ships WHERE shield_resistance = 0.0 AND armor_rating = 0.0;
--
-- =============================================================================

BEGIN;

UPDATE ships
SET shield_resistance = spec.shield_resistance,
    armor_rating = spec.armor_rating
FROM ship_specifications AS spec
WHERE ships.type = spec.type
  AND ships.shield_resistance = 0.0
  AND ships.armor_rating = 0.0;

COMMIT;

-- =============================================================================
-- VERIFICATION (run after commit):
--
-- 1. Post-UPDATE invariant (per the WO's Accept criterion): every ship still
--    reading 0.0/0.0 must be explained by its OWN spec also seeding 0.0/0.0
--    (a legitimately zero-mitigation hull), never a missed backfill:
--
--      SELECT count(*) FROM ships WHERE shield_resistance = 0.0 AND armor_rating = 0.0;
--      -- must equal:
--      SELECT count(*) FROM ships s JOIN ship_specifications spec ON s.type = spec.type
--        WHERE spec.shield_resistance = 0.0 AND spec.armor_rating = 0.0;
--
-- 2. Ships whose type has NO matching ship_specifications row (skipped by
--    construction -- the FROM-join never touches them; count them so the
--    operator has the number, per the WO's "skipped with a count" ask):
--
--      SELECT type, count(*) FROM ships s
--        WHERE NOT EXISTS (SELECT 1 FROM ship_specifications spec WHERE spec.type = s.type)
--        GROUP BY type;
--
-- 3. Spot-check one representative pre-existing hull against its spec (e.g. a
--    LIGHT_FREIGHTER seeds 0.02/0.03 -- ship_specifications_seeder.py:50):
--
--      SELECT s.id, s.type, s.shield_resistance, s.armor_rating
--        FROM ships s WHERE s.type = 'LIGHT_FREIGHTER' ORDER BY s.created_at ASC LIMIT 3;
--
-- 4. Combat smoke (Orchestrator, per the parent WO's Proof section): one
--    attack against a backfilled hull shows mitigation applied (non-zero
--    shield/armor absorption in the combat log), where before the backfill
--    it took full unmitigated damage.
-- =============================================================================
