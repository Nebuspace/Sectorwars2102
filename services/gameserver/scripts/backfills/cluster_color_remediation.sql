-- =============================================================================
-- Cluster nebula-color remediation -- WO-GWQ-CLUSTER-COLOR-REMEDIATION (Path B: backfill)
-- =============================================================================
--
-- WHAT: One-time UPDATE deriving the canon six-color nebula taxonomy
--   (crimson/azure/emerald/violet/amber/obsidian) + its canonical color_hex
--   for every existing cluster still carrying bang's raw 'normal'/'magnetic'
--   nebula_type, from that cluster's ALREADY-PERSISTED quantum_field_strength
--   (mean per-sector density). NO re-import, NO re-derivation of density --
--   pure derivation from a column that is already there.
--
-- WHY: c5c2af8 (WO-SB-QH2) made quantum harvest translate bang's raw
--   'normal'/'magnetic' types into the six canon colors, but ONLY at import
--   time (bang_import_service.py::_finalize_cluster_nebula_fields). Its own
--   spec shipped explicitly "NO data migration ... legacy 'normal'/
--   'magnetic'/NULL rows stay as-is pending Max's backfill-vs-reimport
--   ruling" (audit/tranche-2026-07-02-addendum.md:676,:686). quantum_service.
--   py's _HARVEST_YIELD_BANDS is keyed ONLY on the six canon colors, so every
--   cluster still at 'normal'/'magnetic' rejects POST /quantum/harvest as
--   'uncharted' forever without this script. Ruling: Max ruling #6
--   (2026-07-10) approves Path B (in-place backfill UPDATE) over Path A
--   (stage re-import) -- WO-GWQ-CLUSTER-COLOR-REMEDIATION, audit/
--   enrichment-2026-07-05/new-wos-fresh-residuals.md:37-47.
--
-- DERIVATION SOURCE (mirrored EXACTLY, not re-invented): src/services/
--   nebula_color.py::derive_nebula_color() + NEBULA_COLOR_HEX -- the single
--   shared home both bang_import_service.py and nexus_generation_service.py
--   already call. These are that module's [NO-CANON, flagged to DECISIONS]
--   disjoint cutpoints (canon's own per-color field-strength ranges overlap
--   -- nebula_color.py:39-44), evaluated in the SAME top-down order as the
--   Python if/elif chain (first true branch wins, so each WHEN below is
--   implicitly upper-bounded by the one above it):
--
--     mean_density >= 80   -> crimson  #DC143C   (nebula_color.py:62-63)
--     mean_density >= 60   -> azure    #1E90FF   (nebula_color.py:64-65)
--     mean_density >= 50   -> emerald  #00FF7F   (nebula_color.py:66-67)
--     mean_density >= 40   -> violet   #9370DB   (nebula_color.py:68-69)
--     mean_density >= 20   -> amber    #FF8C00   (nebula_color.py:70-71)
--     otherwise            -> obsidian #2F4F4F   (nebula_color.py:72)
--
--   If NEBULA_COLOR_BOUNDARY_* or NEBULA_COLOR_HEX in nebula_color.py is ever
--   retuned, this script's literals must be updated to match -- a static SQL
--   script cannot import the live Python constants, so parity is manual and
--   must be re-verified against the module before any future re-run.
--
-- PRECONDITIONS:
--   * Run inside a posted DEPLOY-WINDOW -- this mutates live clusters rows.
--   * Idempotent, safe to run twice: the WHERE guard only matches clusters
--     still carrying the raw bang types 'normal' or 'magnetic'. Once
--     remediated, a cluster's nebula_type is one of the six canon colors and
--     the WHERE clause no longer matches it on a second run (0 rows
--     affected).
--   * Derives, never invents: NULL-density clusters (quantum_field_strength
--     IS NULL -- clusters with zero sampled nebula sectors, or legacy rows
--     that predate nebula persistence entirely) are explicitly EXCLUDED and
--     stay exactly as they are, untouched ("NULL-density clusters stay
--     NULL", per the ruling's constraint) -- this script never assigns a
--     color to a cluster bang never measured. A cluster whose nebula_type is
--     already one of the six canon colors (post-c5c2af8 imports) is also
--     untouched -- the WHERE clause only ever matches the two raw legacy
--     values.
--
-- ROW-COUNT EXPECTATIONS: on heimdall's fresh DB (post-c5c2af8, so every
--   freshly-imported cluster already carries a canon color at import time)
--   this is a 0-row no-op by construction -- nothing legacy exists to
--   remediate. On interstitch (has clusters imported before c5c2af8), expect
--   one row per legacy 'normal'/'magnetic' cluster with a non-NULL
--   quantum_field_strength; run the pre-count query below first.
--
-- PRE-RUN COUNT (run first, outside the transaction):
--   SELECT count(*) FROM clusters WHERE nebula_type IN ('normal', 'magnetic');
--   SELECT count(*) FROM clusters WHERE nebula_type IN ('normal', 'magnetic')
--     AND quantum_field_strength IS NULL;
--   -- the second count is how many rows this script will DELIBERATELY skip
--   -- (no density to derive from).
--
-- =============================================================================

BEGIN;

UPDATE clusters
SET nebula_type = CASE
        WHEN quantum_field_strength >= 80 THEN 'crimson'
        WHEN quantum_field_strength >= 60 THEN 'azure'
        WHEN quantum_field_strength >= 50 THEN 'emerald'
        WHEN quantum_field_strength >= 40 THEN 'violet'
        WHEN quantum_field_strength >= 20 THEN 'amber'
        ELSE 'obsidian'
    END,
    color_hex = CASE
        WHEN quantum_field_strength >= 80 THEN '#DC143C'
        WHEN quantum_field_strength >= 60 THEN '#1E90FF'
        WHEN quantum_field_strength >= 50 THEN '#00FF7F'
        WHEN quantum_field_strength >= 40 THEN '#9370DB'
        WHEN quantum_field_strength >= 20 THEN '#FF8C00'
        ELSE '#2F4F4F'
    END
WHERE nebula_type IN ('normal', 'magnetic')
  AND quantum_field_strength IS NOT NULL;

COMMIT;

-- =============================================================================
-- VERIFICATION (run after commit):
--
-- 1. Every legacy raw type is gone, except the deliberately-skipped
--    NULL-density rows (which are UNCHANGED, never colored):
--
--      SELECT count(*) FROM clusters WHERE nebula_type IN ('normal', 'magnetic');
--      -- must equal the "NULL-density skip count" from the pre-run query.
--
-- 2. Every remediated cluster carries a canon color with its own matching
--    hex (no row where color_hex doesn't match its nebula_type):
--
--      SELECT nebula_type, color_hex, count(*) FROM clusters
--        WHERE nebula_type IN ('crimson','azure','emerald','violet','amber','obsidian')
--        GROUP BY nebula_type, color_hex ORDER BY nebula_type;
--      -- exactly one (nebula_type, color_hex) pair per color -- a second
--      -- hex for the same color means a scripting error, not real data.
--
-- 3. Boundary spot-check:
--
--      SELECT id, quantum_field_strength, nebula_type, color_hex FROM clusters
--        WHERE nebula_type IN ('crimson','azure','emerald','violet','amber','obsidian')
--        ORDER BY quantum_field_strength DESC LIMIT 10;
--
-- 4. Live functional proof (Orchestrator, per the parent WO's Proof
--    section): POST /api/v1/quantum/harvest from a member sector of a
--    remediated cluster returns 200 with a shard result, not the
--    'uncharted -- no harvest band' rejection that fired before this script.
-- =============================================================================
