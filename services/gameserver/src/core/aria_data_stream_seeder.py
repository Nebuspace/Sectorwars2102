"""ARIA Data Index seeder (WO-P6-aria-data-index-registry).

Seeds the canonical `aria_data_streams` catalog table from the stream index
at sw2102-docs/DATA_MODELS/aria-data-index.md. Mirrors the
resource_registry_seeder.py / ship_specifications_seeder.py pattern: a
module-level dict keyed by the model's PK, an idempotent query-then-upsert
seed function, called once at startup (src/main.py).

Source count: 21 streams across 6 domains (nav 5, commerce 5, threat 5,
asset 2, social 1, meta 3), transcribed 1:1 from the doc's six domain
tables. Every ``key``/``domain``/``trigger``/``core payload``/``storage``/
``retention`` value below traces directly to a doc row -- nothing invented.

Field provenance (do not invent -- every value below traces to a source):
  * trigger_event: the doc's literal "Trigger" column text for that stream,
    verbatim.
  * description: the doc's per-stream table has exactly ONE prose column
    (Trigger) -- no independent "player-readable one-liner" exists anywhere
    in the doc distinct from it. Rather than fabricate a second sentence,
    description is seeded from the SAME Trigger text as trigger_event. This
    is a doc gap (the registry schema declares both fields but the doc's
    per-stream tables supply only one prose source) -- flagged in this WO's
    report, not silently resolved by inventing new copy.
  * display_name: mechanically Title-Cased from the stream key's segment
    after the domain prefix (e.g. "nav.sector_visit" -> "Sector Visit"),
    written here as literal strings (not algorithmically derived at seed
    time) so the one irregular case (NPC, not "Npc") reads correctly.
  * payload_schema: {"fields": [...]} -- the doc's "Core payload" column,
    comma-split into snake_case tokens. A structural transcription of the
    doc's own field list, not an invented schema (the doc says
    payload_schema should be "JSON-schema of the payload fields" but never
    actually publishes real JSON Schema for any stream -- this is the most
    faithful non-invented rendering of what the doc does publish).
  * storage_table: the doc's literal Storage column value. For the three
    ARIAPersonalMemory-backed streams (threat.combat, meta.dialogue,
    meta.onboarding) the doc's storage cell reads
    "ARIAPersonalMemory (type X)" -- the "(type X)" is dropped here because
    X is always identical to the stream's own key (doc rule 1: "one type
    per stream key"), so keeping it would be redundant with the `key`
    column, not information loss.
  * retention_class / domain: copied verbatim from the doc's exact enum
    tokens (both are given literally in the doc, not derived).
  * transparency_visible=True / version=1 for all 21: doc rule 3 says every
    stream is visible "unless its registry row says otherwise" -- the doc
    calls out zero exceptions among these 21 streams, so all default
    visible. version=1 is the registry's initial ship version for every row
    (doc: "bumped on payload-schema change" -- none has changed yet).

NOT covered by this seeder (out of WO scope, flagged for the report): Lane C
found that the memory_type string literals actually written today by
aria_personal_intelligence_service.py ("combat", "market", "exploration")
do not byte-match any registry key here (closest is "combat" vs.
"threat.combat"; "market" and "exploration" have no ARIAPersonalMemory-
backed doc stream at all -- commerce.trade routes to ARIATradingObservation
and nav.sector_visit routes to ARIAExplorationMap, not ARIAPersonalMemory).
Per the WO's explicit instruction ("if any existing literal has no matching
registry key, STOP and report the mismatch -- don't invent a mapping"),
aria_personal_intelligence_service.py is left untouched.
"""

import logging
from typing import Any, Dict

from sqlalchemy.orm import Session

from src.models.aria_data_stream import (
    ARIADataStream,
    ARIADataStreamDomain,
    ARIADataStreamRetention,
)

logger = logging.getLogger(__name__)

NAV = ARIADataStreamDomain.NAV
COMMERCE = ARIADataStreamDomain.COMMERCE
THREAT = ARIADataStreamDomain.THREAT
ASSET = ARIADataStreamDomain.ASSET
SOCIAL = ARIADataStreamDomain.SOCIAL
META = ARIADataStreamDomain.META

PERMANENT = ARIADataStreamRetention.PERMANENT
ROLLING_90D = ARIADataStreamRetention.ROLLING_90D
BUDGET_PRUNED = ARIADataStreamRetention.BUDGET_PRUNED


def _stream(
    domain: ARIADataStreamDomain,
    display_name: str,
    trigger_event: str,
    fields: list,
    storage_table: str,
    retention_class: ARIADataStreamRetention,
) -> Dict[str, Any]:
    return {
        "domain": domain,
        "display_name": display_name,
        "description": trigger_event,  # see module docstring -- doc gives one prose source
        "trigger_event": trigger_event,
        "payload_schema": {"fields": fields},
        "storage_table": storage_table,
        "retention_class": retention_class,
        "transparency_visible": True,
        "version": 1,
    }


# Canon ARIA data-index (aria-data-index.md "The streams") keyed by stream
# key. Declaration order mirrors the doc's six domain subsections.
ARIA_DATA_STREAMS: Dict[str, Dict[str, Any]] = {
    # --- Navigation (nav) -------------------------------------------------
    "nav.sector_visit": _stream(
        NAV, "Sector Visit", "Move commit into a sector",
        ["sector_id", "timestamp", "visit_count", "hazards_observed_on_arrival"],
        "ARIAExplorationMap", PERMANENT,
    ),
    "nav.warp_discovery": _stream(
        NAV, "Warp Discovery",
        "Warp reveal (scan / traversal_attempt / corp_share / aria_inference)",
        ["warp_layer", "warp_id", "visibility_state", "revealed_via"],
        "player_warp_knowledge", PERMANENT,
    ),
    "nav.formation_sighting": _stream(
        NAV, "Formation Sighting", "First detection of a special formation",
        ["formation_id", "detection_method"],
        "player_formation_knowledge", PERMANENT,
    ),
    "nav.chart_acquisition": _stream(
        NAV, "Chart Acquisition",
        "Chart packet learned (purchase / corp share / probe return)",
        ["source", "sector_set", "learned_at"],
        "player_known_sectors", PERMANENT,
    ),
    "nav.echo_outcome": _stream(
        NAV, "Echo Outcome",
        "Conjectural hop confirmed or refuted (ADR-0092 S6 speculative plots)",
        ["predicted_edge", "actual_outcome", "confidence_at_prediction"],
        "new stream table", ROLLING_90D,
    ),
    # --- Commerce (commerce) ------------------------------------------------
    "commerce.port_catalog": _stream(
        COMMERCE, "Port Catalog", "Dock / market view",
        ["station_id", "commodity_set_offered", "buy_sell_directions", "port_class"],
        "ARIAMarketIntelligence (per-commodity rows)", BUDGET_PRUNED,
    ),
    "commerce.price_observation": _stream(
        COMMERCE, "Price Observation", "Market view (deduped per window)",
        ["station_id", "commodity", "price", "quantity", "timestamp"],
        "ARIAMarketIntelligence.price_observations", BUDGET_PRUNED,
    ),
    "commerce.trade": _stream(
        COMMERCE, "Trade", "Trade completion",
        ["commodity", "action", "stations", "quantity", "price", "profit", "outcome"],
        "ARIATradingObservation", BUDGET_PRUNED,
    ),
    "commerce.haggle": _stream(
        COMMERCE, "Haggle", "Haggling exchange resolves",
        ["station_id", "commodity", "style_used", "offer_path", "outcome"],
        "new stream table", BUDGET_PRUNED,
    ),
    "commerce.contract": _stream(
        COMMERCE, "Contract", "Contract accepted / completed / failed / expired",
        ["contract_id", "type", "counterparty_station", "outcome", "net"],
        "new stream table", BUDGET_PRUNED,
    ),
    # --- Threat (threat) ------------------------------------------------
    "threat.combat": _stream(
        THREAT, "Combat", "Combat resolution involving the player",
        ["opponent_ship_type", "npc_archetype_or_player_flag", "outcome",
         "hull_at_exit", "weapon_used", "sector"],
        "ARIAPersonalMemory", BUDGET_PRUNED,
    ),
    "threat.drone_encounter": _stream(
        THREAT, "Drone Encounter",
        "Sector-defense or hostile drones engaged or observed",
        ["sector_id", "drone_count_band", "owner_class", "outcome"],
        "new stream table", BUDGET_PRUNED,
    ),
    "threat.mine_encounter": _stream(
        THREAT, "Mine Encounter", "Nav-hazard mine detected or struck",
        ["sector_id", "hazard_type", "detected_vs_struck", "damage"],
        "new stream table", BUDGET_PRUNED,
    ),
    "threat.npc_sighting": _stream(
        THREAT, "NPC Sighting", "NPC presence observed on sector arrival",
        ["sector_id", "archetype", "faction", "disposition", "timestamp"],
        "new stream table", ROLLING_90D,
    ),
    "threat.player_sighting": _stream(
        THREAT, "Player Sighting", "Another player's ship observed in-sector",
        ["sector_id", "ship_type", "displayed_name", "timestamp"],
        "new stream table", ROLLING_90D,
    ),
    # --- Assets (asset) ---------------------------------------------------
    "asset.ship_event": _stream(
        ASSET, "Ship Event",
        "Ship acquired / upgraded / damaged / repaired / insured / lost",
        ["ship_id", "event_class", "before_after_summary"],
        "new stream table", BUDGET_PRUNED,
    ),
    "asset.cargo_event": _stream(
        ASSET, "Cargo Event", "Cargo loaded / jettisoned / salvaged / stolen",
        ["commodity", "quantity", "cause", "sector"],
        "new stream table", BUDGET_PRUNED,
    ),
    # --- Social (social) ----------------------------------------------------
    "social.team_event": _stream(
        SOCIAL, "Team Event", "Team join / leave; knowledge share received",
        ["team_id", "event_class", "share_provenance"],
        "new stream table", PERMANENT,
    ),
    # --- Meta (meta) --------------------------------------------------------
    "meta.dialogue": _stream(
        META, "Dialogue", "ARIA exchange (question asked, answer mode local/deep)",
        ["intent_class", "mode", "resonance_spent", "timestamp"],
        "ARIAPersonalMemory", BUDGET_PRUNED,
    ),
    "meta.recommendation_feedback": _stream(
        META, "Recommendation Feedback",
        "Recommendation accepted / dismissed / outcome observed",
        ["recommendation_id", "action", "realized_outcome"],
        "ai_recommendations feedback fields", BUDGET_PRUNED,
    ),
    "meta.onboarding": _stream(
        META, "Onboarding", "First-login dialogue milestones",
        ["milestone_key", "timestamp"],
        "ARIAPersonalMemory", PERMANENT,
    ),
}


def seed_aria_data_streams(db: Session) -> int:
    """Idempotently upsert :data:`ARIA_DATA_STREAMS` into the
    `aria_data_streams` table. Query-then-upsert keyed on `key` (the PK).
    Mirrors seed_resource_registry / seed_ship_specifications (single-
    threaded startup seed). Returns the number of catalog entries processed
    (created + updated)."""
    processed = 0
    for key, entry in ARIA_DATA_STREAMS.items():
        existing = db.query(ARIADataStream).filter(ARIADataStream.key == key).first()

        if existing is None:
            db.add(ARIADataStream(key=key, **entry))
            logger.info("Created ARIA data-stream registry entry for %s", key)
        else:
            for field, value in entry.items():
                setattr(existing, field, value)
            logger.info("Updated ARIA data-stream registry entry for %s", key)
        processed += 1

    db.commit()
    logger.info("ARIA data-stream registry seeding complete: %d processed", processed)
    return processed
