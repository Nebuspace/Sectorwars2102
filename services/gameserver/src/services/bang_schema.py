"""Path B bang.* → Universe JSON reconstruction (LEG-2252).

This module is glue, not a second importer. It rebuilds the Universe JSON
shape ``BangImportService.translate`` already consumes (inverse of
``sw2102-bang/src/db-writer.ts``). Persistence stays on ``translate`` /
``galaxy_validation`` / ``apply`` / ``apply_additional_region``.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


def _json_load(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return json.loads(value.decode("utf-8"))
    if isinstance(value, str):
        return json.loads(value)
    return value


def _row_get(row: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    return default


def universe_from_bang_rows(  # noqa: C901 — reconstructs bang.* row groups into Universe JSON
    universe: Mapping[str, Any],
    *,
    sectors: Sequence[Mapping[str, Any]],
    warps: Sequence[Mapping[str, Any]],
    special_locations: Sequence[Mapping[str, Any]] = (),
    ports: Sequence[Mapping[str, Any]] = (),
    planets: Sequence[Mapping[str, Any]] = (),
    nebulae: Sequence[Mapping[str, Any]] = (),
    clusters: Sequence[Mapping[str, Any]] = (),
    special_formations: Sequence[Mapping[str, Any]] = (),
    npc_rosters: Sequence[Mapping[str, Any]] = (),
) -> Dict[str, Any]:
    """Rebuild Universe JSON from bang-schema row dicts.

    Missing optional columns (``is_latent``, port ``black_market`` / docking
    slips) are omitted — same graceful-on-absent contract as ingest.
    """
    config = _json_load(_row_get(universe, "config")) or {}
    if not isinstance(config, dict):
        config = {}

    ports_by_sector: Dict[Any, Mapping[str, Any]] = {}
    for port in ports:
        sid = _row_get(port, "sector_id")
        if sid is not None:
            ports_by_sector[sid] = port

    planets_by_sector: Dict[Any, List[Mapping[str, Any]]] = {}
    for planet in planets:
        sid = _row_get(planet, "sector_id")
        if sid is None:
            continue
        planets_by_sector.setdefault(sid, []).append(planet)

    nebulae_by_sector: Dict[Any, Mapping[str, Any]] = {}
    for nebula in nebulae:
        sid = _row_get(nebula, "sector_id")
        if sid is not None:
            nebulae_by_sector[sid] = nebula

    warps_from: Dict[int, List[int]] = {}
    warp_list: List[Dict[str, Any]] = []
    for warp in warps:
        src = int(_row_get(warp, "from_sector", "from"))
        dst = int(_row_get(warp, "to_sector", "to"))
        one_way = bool(_row_get(warp, "one_way", "oneWay", default=False))
        item: Dict[str, Any] = {"from": src, "to": dst, "oneWay": one_way}
        latent = _row_get(warp, "is_latent", "isLatent")
        if latent is not None:
            item["isLatent"] = bool(latent)
        warp_list.append(item)
        warps_from.setdefault(src, []).append(dst)
        if not one_way:
            warps_from.setdefault(dst, []).append(src)

    sector_payload: Dict[str, Any] = {}
    for sector in sectors:
        number = int(_row_get(sector, "sector_number", "id"))
        sid = _row_get(sector, "id", default=number)
        pos_x = int(_row_get(sector, "pos_x", default=0))
        pos_y = int(_row_get(sector, "pos_y", default=0))
        pos_z = int(_row_get(sector, "pos_z", default=0))
        entry: Dict[str, Any] = {
            "id": number,
            "position": {"x": pos_x, "y": pos_y, "z": pos_z},
            "warps": list(warps_from.get(number, [])),
            "beacon": _row_get(sector, "beacon"),
            "explored": bool(_row_get(sector, "explored", default=False)),
            "planets": [],
            "navHazards": [],
        }
        resources = _json_load(_row_get(sector, "resources"))
        if resources is not None:
            entry["resources"] = resources

        port = ports_by_sector.get(sid)
        if port is not None:
            port_json: Dict[str, Any] = {
                "name": _row_get(port, "name"),
                "class": int(_row_get(port, "class", default=0)),
                "commodities": _json_load(_row_get(port, "commodities")) or {},
            }
            if _row_get(port, "is_spacedock", "isSpaceDock"):
                port_json["isSpaceDock"] = True
            black_market = _row_get(port, "black_market")
            if black_market:
                port_json["black_market"] = True
            tradedock_tier = _row_get(port, "tradedock_tier", "tradedockTier")
            if tradedock_tier is not None:
                port_json["tradedockTier"] = tradedock_tier
            docking_slips = _json_load(_row_get(port, "docking_slips", "dockingSlips"))
            if docking_slips is not None:
                port_json["dockingSlips"] = docking_slips
            entry["port"] = port_json

        for planet in planets_by_sector.get(sid, []):
            planet_json: Dict[str, Any] = {
                "name": _row_get(planet, "name"),
                "type": _row_get(planet, "type"),
                "owner": _row_get(planet, "owner"),
                "habitabilityScore": int(_row_get(planet, "habitability_score", "habitabilityScore", default=0)),
                "maxPopulation": int(_row_get(planet, "max_population", "maxPopulation", default=0)),
                "maxColonists": int(_row_get(planet, "max_colonists", "maxColonists", default=1000)),
                "ore": int(_row_get(planet, "ore", default=0)),
                "organics": int(_row_get(planet, "organics", default=0)),
                "equipment": int(_row_get(planet, "equipment", default=0)),
                "colonists": int(_row_get(planet, "colonists", default=0)),
            }
            citadel_level = _row_get(planet, "citadel_level")
            if citadel_level is not None:
                planet_json["citadel"] = {
                    "level": int(citadel_level),
                    "droneCapacity": int(_row_get(planet, "citadel_drone_capacity", default=0)),
                    "safeContents": int(_row_get(planet, "citadel_safe_contents", default=0)),
                    "droneInventory": int(_row_get(planet, "citadel_drone_inventory", default=0)),
                }
            entry["planets"].append(planet_json)

        nebula = nebulae_by_sector.get(sid)
        if nebula is not None:
            entry["nebula"] = {
                "type": _row_get(nebula, "type"),
                "density": int(_row_get(nebula, "density", default=1)),
            }

        sector_payload[str(number)] = entry

    cluster_payload: List[Dict[str, Any]] = []
    config_max_warps = int(config.get("maxWarps", 6) or 6)
    for cluster in clusters:
        start = int(_row_get(cluster, "sector_range_start", "sectorRangeStart"))
        end = int(_row_get(cluster, "sector_range_end", "sectorRangeEnd"))
        cluster_payload.append(
            {
                "id": int(_row_get(cluster, "cluster_number", "id")),
                "name": _row_get(cluster, "name"),
                "type": _row_get(cluster, "type"),
                "sectorRangeStart": start,
                "sectorRangeEnd": end,
                "sectorCount": end - start + 1,
                "coords": {
                    "x": int(_row_get(cluster, "coords_x", default=0)),
                    "y": int(_row_get(cluster, "coords_y", default=0)),
                    "z": int(_row_get(cluster, "coords_z", default=0)),
                },
                "warpStability": float(_row_get(cluster, "warp_stability", "warpStability", default=1.0)),
                "economicValue": int(_row_get(cluster, "economic_value", "economicValue", default=0)),
                "isDiscovered": bool(_row_get(cluster, "is_discovered", "isDiscovered", default=True)),
                "isHidden": bool(_row_get(cluster, "is_hidden", "isHidden", default=False)),
                "maxWarps": config_max_warps,
                "recommendedShipClass": "light_freighter",
            }
        )

    formation_payload: List[Dict[str, Any]] = []
    for formation in special_formations:
        interiors = _row_get(formation, "interior_sector_numbers", "interiorSectorIds", default=[]) or []
        if isinstance(interiors, str):
            interiors = _json_load(interiors) or []
        formation_payload.append(
            {
                "id": int(_row_get(formation, "formation_number", "id")),
                "type": _row_get(formation, "type"),
                "name": _row_get(formation, "name"),
                "anchorSectorId": int(_row_get(formation, "anchor_sector_number", "anchorSectorId")),
                "interiorSectorIds": [int(i) for i in interiors],
                "properties": _json_load(_row_get(formation, "properties")) or {},
                "clusterId": int(_row_get(formation, "cluster_number", "clusterId", default=1)),
                "endpointClusterId": _row_get(
                    formation, "endpoint_cluster_number", "endpointClusterId"
                ),
                "isDiscovered": bool(_row_get(formation, "is_discovered", "isDiscovered", default=False)),
                "isHidden": bool(_row_get(formation, "is_hidden", "isHidden", default=False)),
            }
        )

    roster_payload: List[Dict[str, Any]] = []
    for roster in npc_rosters:
        roster_payload.append(
            {
                "id": int(_row_get(roster, "roster_number", "id")),
                "kind": _row_get(roster, "kind"),
                "factionCode": _row_get(roster, "faction_code", "factionCode"),
                "targetCount": int(_row_get(roster, "target_count", "targetCount", default=1)),
                "hostSectorId": int(_row_get(roster, "host_sector_number", "hostSectorId")),
                "namePool": _json_load(_row_get(roster, "name_pool", "namePool")) or [],
                "defaultLodgingId": _row_get(roster, "default_lodging_id", "defaultLodgingId"),
            }
        )

    special_location_payload: List[Dict[str, Any]] = []
    for location in special_locations:
        special_location_payload.append(
            {
                "type": _row_get(location, "type"),
                "sectorId": int(_row_get(location, "sector_number", "sectorId")),
            }
        )

    fedspace = config.get("fedspace")
    fedspace_sectors: List[int] = []
    if isinstance(fedspace, int) and fedspace > 0:
        fedspace_sectors = list(range(1, fedspace + 1))
    elif isinstance(fedspace, list):
        fedspace_sectors = [int(x) for x in fedspace]

    created_at = _row_get(universe, "created_at", "createdAt")
    payload: Dict[str, Any] = {
        "version": str(_row_get(universe, "version")),
        "seed": int(_row_get(universe, "seed")),
        "totalSectors": int(_row_get(universe, "total_sectors", "totalSectors")),
        "config": config,
        "sectors": sector_payload,
        "warps": warp_list,
        "clusters": cluster_payload,
        "specialFormations": formation_payload,
        "npcRosters": roster_payload,
        "specialLocations": special_location_payload,
        "fedspaceSectors": fedspace_sectors,
    }
    if created_at is not None:
        payload["createdAt"] = (
            created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
        )
    return payload


def config_fingerprint(config: Mapping[str, Any]) -> str:
    """Stable JSON fingerprint for Path B idempotency (seed is stored separately)."""
    import hashlib

    blob = json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def stamp_region_snapshot(
    snapshot: Optional[Mapping[str, Any]],
    region_type: str,
    *,
    region_id: str,
    universe: Mapping[str, Any],
) -> Dict[str, Any]:
    """Merge a Path B region into ``Galaxy.bang_snapshot['regions']``.

    ``apply_additional_region`` only appends ``additional_regions`` (Path A
    player_owned add — no universe blob). Singleton Path B regions
    (terran_space / central_nexus) need the universe blob so the next
    invocation can idempotent-skip via :func:`region_already_imported`.
    """
    snap: Dict[str, Any] = dict(snapshot or {})
    regions = dict(snap.get("regions") or {})
    entry = dict(regions.get(region_type) or {})
    entry["region_id"] = str(region_id)
    entry["universe"] = dict(universe)
    regions[region_type] = entry
    snap["regions"] = regions
    return snap


def region_already_imported(
    snapshot: Optional[Mapping[str, Any]],
    region_type: str,
    *,
    seed: int,
    config: Mapping[str, Any],
) -> bool:
    """True when Galaxy.bang_snapshot already holds this region at the same seed+config."""
    if not snapshot:
        return False
    regions = snapshot.get("regions") or {}
    entry = regions.get(region_type)
    if not isinstance(entry, dict):
        additional = snapshot.get("additional_regions") or []
        for item in additional:
            if isinstance(item, dict) and item.get("region_type") == region_type:
                entry = item
                break
        else:
            return False
    universe = entry.get("universe") or {}
    if int(universe.get("seed", -1)) != int(seed):
        return False
    stored = universe.get("config") or {}
    return config_fingerprint(stored) == config_fingerprint(config)


def mappings_from_result(rows: Iterable[Any]) -> List[Dict[str, Any]]:
    """Normalize SQLAlchemy Row / mapping objects to plain dicts."""
    out: List[Dict[str, Any]] = []
    for row in rows:
        if isinstance(row, Mapping):
            out.append(dict(row))
        elif hasattr(row, "_mapping"):
            out.append(dict(row._mapping))
        else:
            out.append(dict(row))
    return out
