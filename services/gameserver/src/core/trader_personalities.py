"""Trader-personality archetypes and the station-class → archetype mapping.

Single source of truth for the `Station.trader_personality` JSONB document that
drives the numerical haggling engine (`services/haggle_service.py`).

Canon
-----
* Shape + archetype defaults: ``DATA_MODELS/jsonb-schema.md`` § ``Station.trader_personality``
  (the authoritative table — *not* the ``haggling.md`` archetype table, which the
  ADR-0079 reconcile note defers to the jsonb-schema values).
* Difficulty authority: ``ADR-0079`` point 5 — ``haggling_difficulty`` int 1–10
  is the single source; the archetype only *sets* it.
* Field reconcile (ADR-0079 "Schema reconciliation"): ``memory_duration_days``
  (NOT ``memory_duration``) in [7, 90]; ``trust_level`` in [-1000, 1000] (NOT a
  50 default — defaults to 0).
* Seeding by station class: ``haggling.md`` § Port personality types describes the
  class→archetype placement (Federation @ core Class 0–4, Border @ mixed frontier-
  edge, Frontier @ outer rim, Luxury @ Class 10 + prestige 6–7, Black Market @
  Class 8). 100% of live stations carry the BORDER default, so archetype-driven
  difficulty is a no-op until this seeding pass derives real personalities — the
  prerequisite ADR-0079 flagged.

Why a class→archetype map: the bang importer / station model do not carry a
faction-control or prestige signal at creation, so the station **class** is the
deterministic, available proxy the canon itself uses for archetype placement.
"""

from __future__ import annotations

import enum
from typing import Any, Dict, List


# ── Archetype enum (mirrors models.station.TraderPersonalityType values) ──────
class TraderArchetype(str, enum.Enum):
    FEDERATION = "FEDERATION"
    BORDER = "BORDER"
    FRONTIER = "FRONTIER"
    LUXURY = "LUXURY"
    BLACK_MARKET = "BLACK_MARKET"


# Canonical bounds (jsonb-schema.md "Constraints").
DIFFICULTY_MIN = 1
DIFFICULTY_MAX = 10
MEMORY_DAYS_MIN = 7
MEMORY_DAYS_MAX = 90
TRUST_MIN = -1000
TRUST_MAX = 1000

# Max ruling #7: per-NPC personality persists and the NPC remembers the player
# for 90 real days. The archetype table below sets a per-archetype memory window;
# Max #7 pins the *system* memory horizon at the canonical maximum (90 days),
# which is also the [7, 90] ceiling. ``MEMORY_DURATION_DAYS`` is the value the
# haggle engine uses for the 90-day memory contract regardless of archetype.
MEMORY_DURATION_DAYS = 90


# ── Archetype defaults (DATA_MODELS/jsonb-schema.md authoritative table) ──────
#
# | type        | haggling_difficulty | preferred_appeal_types  | memory_duration_days |
# | Federation  | 3                   | procedural, compliance  | 30                   |
# | Border      | 5                   | economic, personal      | 30                   |
# | Frontier    | 7                   | personal, risk          | 14                   |
# | Luxury      | 8                   | cultural, aesthetic     | 60                   |
# | Black Market| 9                   | risk, discretion        | 7                    |
#
# trust_level defaults to 0; quirks defaults to [].
_ARCHETYPE_DEFAULTS: Dict[TraderArchetype, Dict[str, Any]] = {
    TraderArchetype.FEDERATION: {
        "haggling_difficulty": 3,
        "preferred_appeal_types": ["procedural", "compliance"],
        "memory_duration_days": 30,
    },
    TraderArchetype.BORDER: {
        "haggling_difficulty": 5,
        "preferred_appeal_types": ["economic", "personal"],
        "memory_duration_days": 30,
    },
    TraderArchetype.FRONTIER: {
        "haggling_difficulty": 7,
        "preferred_appeal_types": ["personal", "risk"],
        "memory_duration_days": 14,
    },
    TraderArchetype.LUXURY: {
        "haggling_difficulty": 8,
        "preferred_appeal_types": ["cultural", "aesthetic"],
        "memory_duration_days": 60,
    },
    TraderArchetype.BLACK_MARKET: {
        "haggling_difficulty": 9,
        "preferred_appeal_types": ["risk", "discretion"],
        "memory_duration_days": 7,
    },
}

# Valid appeal-type vocabulary (jsonb-schema.md "Constraints").
VALID_APPEAL_TYPES = frozenset(
    {
        "procedural",
        "compliance",
        "economic",
        "personal",
        "cultural",
        "aesthetic",
        "risk",
        "discretion",
    }
)


def archetype_for_station_class(station_class_value: int) -> TraderArchetype:
    """Map a station class (0–11) to its trader archetype.

    Placement follows ``FEATURES/economy/haggling.md`` § Port personality types:

    * **Federation** — high-faction-standing core, Class 0–4.
    * **Border**     — mixed-faction frontier-edge, the baseline (Class 5–7).
    * **Frontier**   — outer rim (no dedicated class signal in code; Border is
                       the safe baseline for ordinary frontier trade stations).
    * **Luxury**     — Class 10 hubs (and prestige Class 11 tech specialists).
    * **Black Market** — Class 8 (Black Hole) and Class 9 (Nova) shadow ports.

    The class is the only archetype-relevant signal available at station creation
    (no faction-control / prestige field on the model), so it is the deterministic
    proxy. Unknown / out-of-range classes fall back to Border (the canon baseline).
    """
    c = station_class_value
    if c in (8, 9):
        return TraderArchetype.BLACK_MARKET
    if c in (10, 11):
        return TraderArchetype.LUXURY
    if 0 <= c <= 4:
        return TraderArchetype.FEDERATION
    if 5 <= c <= 7:
        return TraderArchetype.BORDER
    return TraderArchetype.BORDER


def default_personality(archetype: TraderArchetype) -> Dict[str, Any]:
    """Return a fresh ``trader_personality`` JSONB doc for an archetype.

    Always returns the canonical, fully-reconciled shape:
    ``{type, haggling_difficulty, preferred_appeal_types, memory_duration_days,
       trust_level (0), quirks ([]), player_memory ({})}``.

    ``player_memory`` is the per-player haggle-history sub-document (keyed by
    player UUID string) the haggle engine maintains for the 90-day memory +
    per-player trust contract (Max #7 / WO-BO step D). It is additive to the
    documented schema and ignored by everything except the haggle engine.
    """
    spec = _ARCHETYPE_DEFAULTS[archetype]
    return {
        "type": archetype.value,
        "haggling_difficulty": spec["haggling_difficulty"],
        "preferred_appeal_types": list(spec["preferred_appeal_types"]),
        "memory_duration_days": spec["memory_duration_days"],
        "trust_level": 0,
        "quirks": [],
        "player_memory": {},
    }


def build_personality_for_class(station_class_value: int) -> Dict[str, Any]:
    """Build a canonical ``trader_personality`` for a station of the given class."""
    return default_personality(archetype_for_station_class(station_class_value))


def _coerce_archetype(value: Any) -> TraderArchetype:
    """Best-effort coerce a stored ``type`` string to a TraderArchetype.

    Accepts the canonical enum values (``FEDERATION`` …), the model enum names,
    and the title-cased ``haggling.md`` labels (``Black Market``) defensively.
    Falls back to BORDER (the canon baseline) on anything unrecognized.
    """
    if isinstance(value, TraderArchetype):
        return value
    if not isinstance(value, str):
        return TraderArchetype.BORDER
    key = value.strip().upper().replace(" ", "_").replace("-", "_")
    try:
        return TraderArchetype(key)
    except ValueError:
        return TraderArchetype.BORDER


def _clamp(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def normalize_personality(raw: Dict[str, Any] | None) -> Dict[str, Any]:
    """Reconcile a (possibly legacy) ``trader_personality`` doc to canon shape.

    ADR-0079 schema-reconcile: legacy rows carry the OLD shape
    (``memory_duration`` not ``memory_duration_days``; ``trust_level`` default
    50 not 0; lowercase ``type`` like "BORDER"). This returns a clean canonical
    document, preserving real per-player memory if present.

    Behaviour:
    * Missing / empty → full archetype default (BORDER).
    * Out-of-band ``haggling_difficulty`` clamped to [1, 10].
    * ``memory_duration`` (legacy) migrated to ``memory_duration_days``,
      clamped to [7, 90].
    * ``trust_level`` clamped to [-1000, 1000]; the legacy default of exactly
      50 (the old hard-coded default) is reset to 0, but any other legacy value
      is preserved (could be accrued trust).
    * ``preferred_appeal_types`` filtered to the valid vocabulary; falls back to
      the archetype default when nothing valid remains.
    * Unknown keys other than ``player_memory`` are dropped.

    The function is idempotent: feeding it a canonical doc returns an equal doc.
    """
    if not raw or not isinstance(raw, dict):
        return default_personality(TraderArchetype.BORDER)

    archetype = _coerce_archetype(raw.get("type"))
    spec = _ARCHETYPE_DEFAULTS[archetype]

    difficulty = _clamp(
        raw.get("haggling_difficulty"),
        DIFFICULTY_MIN,
        DIFFICULTY_MAX,
        spec["haggling_difficulty"],
    )

    # memory_duration_days, falling back to the legacy memory_duration key.
    memory_raw = raw.get("memory_duration_days", raw.get("memory_duration"))
    memory_days = _clamp(
        memory_raw, MEMORY_DAYS_MIN, MEMORY_DAYS_MAX, spec["memory_duration_days"]
    )

    # trust_level: the legacy hard-coded default of exactly 50 is meaningless
    # (it predates the [-1000, 1000] scale) → reset to the canonical 0. Any
    # other value is real and preserved (clamped to bounds).
    trust_raw = raw.get("trust_level", 0)
    if trust_raw == 50:
        trust_level = 0
    else:
        trust_level = _clamp(trust_raw, TRUST_MIN, TRUST_MAX, 0)

    appeals_raw = raw.get("preferred_appeal_types") or []
    appeals = [a for a in appeals_raw if a in VALID_APPEAL_TYPES]
    if not appeals:
        appeals = list(spec["preferred_appeal_types"])

    quirks_raw = raw.get("quirks") or []
    quirks = [q for q in quirks_raw if isinstance(q, str)]

    player_memory = raw.get("player_memory")
    if not isinstance(player_memory, dict):
        player_memory = {}

    return {
        "type": archetype.value,
        "haggling_difficulty": difficulty,
        "preferred_appeal_types": appeals,
        "memory_duration_days": memory_days,
        "trust_level": trust_level,
        "quirks": quirks,
        "player_memory": player_memory,
    }


def needs_reseed(raw: Dict[str, Any] | None, station_class_value: int) -> bool:
    """True when an existing station's personality is a no-op default or stale shape.

    The seeding backfill (WO-BO step B) targets exactly these: rows still carrying
    the legacy/model BORDER default while their class implies a different archetype,
    or rows in the OLD shape (legacy ``memory_duration`` key / ``trust_level`` == 50
    / no ``player_memory``). Rows already carrying a class-correct, canonical-shape
    personality (with real per-player memory) are left untouched.
    """
    if not raw or not isinstance(raw, dict):
        return True
    # Old shape signals.
    if "memory_duration_days" not in raw or "player_memory" not in raw:
        return True
    if raw.get("memory_duration") is not None:
        return True
    if raw.get("trust_level") == 50:
        return True
    # Archetype mismatch: the stored type doesn't match what the class implies AND
    # there is no accrued per-player memory to preserve (a deliberately overridden
    # personality with memory is left alone).
    stored = _coerce_archetype(raw.get("type"))
    expected = archetype_for_station_class(station_class_value)
    has_memory = bool(raw.get("player_memory"))
    if stored != expected and not has_memory:
        return True
    return False


def reseed_personality(
    raw: Dict[str, Any] | None, station_class_value: int
) -> Dict[str, Any]:
    """Produce a class-correct, canonical-shape personality, preserving real
    per-player memory + accrued trust from a prior doc when present.

    Used by the backfill: a station whose personality is a no-op BORDER default
    gets its archetype-correct difficulty/appeals/memory window, but any genuine
    per-player haggle history is carried forward (memory must survive a reseed).
    """
    base = build_personality_for_class(station_class_value)
    if isinstance(raw, dict):
        prior_mem = raw.get("player_memory")
        if isinstance(prior_mem, dict) and prior_mem:
            base["player_memory"] = prior_mem
        # Preserve a non-default accrued trust (anything other than the legacy 50).
        prior_trust = raw.get("trust_level")
        if isinstance(prior_trust, int) and prior_trust not in (0, 50):
            base["trust_level"] = max(TRUST_MIN, min(TRUST_MAX, prior_trust))
        # Preserve any custom quirks an operator set.
        prior_quirks = raw.get("quirks")
        if isinstance(prior_quirks, list) and prior_quirks:
            base["quirks"] = [q for q in prior_quirks if isinstance(q, str)]
    return base
