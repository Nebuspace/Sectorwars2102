"""Station-protection security-tier derivation (WO-STN-SEC-1, FEATURES/economy/
station-protection.md § Security tiers).

Single source of truth for :func:`_derive_station_security_tier` — extracted
from ``bang_import_service`` (WO-TD-NEXGEN-1) so a second galaxy-generation
path (``nexus_generation_service``) can seed the exact same tier rule without
importing the whole bang translator module (which pulls in the ``docker`` SDK
at module scope — an unwanted hard dependency for the live in-process
generator). ``bang_import_service`` now imports this function rather than
defining it locally; behavior is unchanged (see
``tests/unit/test_station_security_seeding.py``, which still imports it via
``src.services.bang_import_service``).

Station.security has ZERO writers outside the paths that call this helper
(model docstring: defaults "are SEEDED by the larger system, NOT here") —
every station seeded through a caller of this function carries an explicit
tier so the combat_service.py Guarantee #1 gate
(ERR_DOCKED_SHIP_PROTECTED at security_rank >= basic) can actually fire.

Canon pins exactly three literal anchors to Standard/Premium: "Federation
Capital station" (Terran Space's CLASS_0 hub, Earth Station), "Nexus
Starport Prime" (Central Nexus's CLASS_0 hub), and "Terran Space hub
stations" (the region's other service hubs — the CLASS_11 Stardock
SpaceDock, the Tier-A TradeDocks). Frontier/lawless CLUSTERS get "none"
("frontier outposts...lawless ports" — the ClusterType vocabulary already
used for hazard/resource biasing, WO-GX1).

Ordinary CLASS_1-11 NPC ports (in ANY region, including the thousands of
background ports inside Terran Space/Central Nexus that aren't a named
anchor): canon states only "Player-owned stations default to Basic" and
is silent here — WO-STN-SEC-1's original report shipped a uniform "basic"
floor. **Ratified 2026-08-04 (human):** replaced with a per-class gradient,
matching the docking-slips precedent's own class-banding
(docking-slips.md § Per-station-class slip counts: 0-2 / 3-6 / 7-10 / 11).
Higher station classes carry higher default security. Premium stays
reserved for the three literal anchors above (never granted by class
alone) — the gradient's ceiling is "standard":
  - CLASS_1-2 (basic trading) -> basic
  - CLASS_3-6 (mid-tier trading) -> basic
  - CLASS_7-10 (premium / refining) -> standard
  - CLASS_11 (Premium Tech Specialist) -> standard
"""
from __future__ import annotations

from typing import Optional

from src.models.cluster import ClusterType
from src.models.station import StationClass

_OPERATOR_MANAGED_REGION_TYPES = ("terran_space", "central_nexus")
_LAWLESS_CLUSTER_TYPES = (ClusterType.FRONTIER_OUTPOST, ClusterType.CONTESTED)

# Per-class default gradient for ordinary CLASS_1-11 NPC ports, mirroring
# docking-slips.md's own class-banding (0-2 / 3-6 / 7-10 / 11). Premium is
# deliberately absent here -- it stays reserved for the three literal
# canon anchors handled above this table in _derive_station_security_tier.
_CLASS_GRADIENT_HIGH_TIERS = (
    StationClass.CLASS_7,
    StationClass.CLASS_8,
    StationClass.CLASS_9,
    StationClass.CLASS_10,
    StationClass.CLASS_11,
)


def _derive_station_security_tier(
    *,
    region_type: str,
    cluster_type: Optional[ClusterType],
    station_class: StationClass,
    is_spacedock: bool,
    tradedock_tier: Optional[str],
) -> str:
    """Return the tier string ("none"/"basic"/"standard"/"premium") to seed
    on a freshly-created station's ``Station.security`` JSONB. Pure/DB-free —
    see tests/unit/test_station_security_seeding.py."""
    if region_type in _OPERATOR_MANAGED_REGION_TYPES:
        if station_class == StationClass.CLASS_0:
            # Central Nexus's CLASS_0 hub = Nexus Starport Prime (premium);
            # Terran Space's CLASS_0 hub = Federation Capital station / Earth
            # Station (standard).
            return "premium" if region_type == "central_nexus" else "standard"
        if is_spacedock or tradedock_tier == "A":
            return "standard"  # Terran Space / Central Nexus hub stations
    if cluster_type in _LAWLESS_CLUSTER_TYPES:
        return "none"
    if station_class in _CLASS_GRADIENT_HIGH_TIERS:
        return "standard"
    return "basic"
