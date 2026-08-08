#!/usr/bin/env python3
"""
Idempotent repair: seed canonical TradeDocks into a pre-existing galaxy.

Galaxies imported before ADR-0041 Phase 10.5 landed in the BANG translator
have zero TradeDocks, which makes ship construction (and therefore the Warp
Jumper, the galaxy's only craft-only hull) unreachable. This plants the
per-region quota (tradedock-shipyard #galaxy-generation-seeding):

  - Terran Space:   1 Tier-A   (Federation zone — local sectors 1-99)
  - Central Nexus:  3          (1 Tier-A + 2 Tier-B, upper half of region)
  - Player regions: none       (owner-funded only, never auto-seeded)

Run inside the gameserver container:
    docker compose exec gameserver python repair_tradedocks.py

Skips any region that already has TradeDocks (tradedock_tier NOT NULL).
"""

import random
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import src.models  # register all mappers
from src.core.commodity_economy import base_price as _commodity_base_price
from src.core.database import SessionLocal
from src.core.station_class_map import apply_class_pattern
from src.core.market_bootstrap import build_market_prices
from src.models.region import Region
from src.models.sector import Sector
from src.models.station import Station, StationClass, StationType, StationStatus

logger = logging.getLogger(__name__)

QUOTAS = {
    "terran_space": ["A"],
    "central_nexus": ["A", "B", "B"],
}
NAMES = {
    "A": ["TradeDock Prime", "TradeDock Apex"],
    "B": ["TradeDock Meridian", "TradeDock Crucible", "TradeDock Bastion"],
}
# Tier-A flagships are unique per region (keep in sync with
# bang_import_service._TRADEDOCK_TIER_A_NAMES_BY_REGION): Terran Space keeps
# 'TradeDock Prime'; the Central Nexus flagship is 'TradeDock Nexus Prime'.
TIER_A_NAMES_BY_REGION = {
    "terran_space": ["TradeDock Prime", "TradeDock Apex"],
    "central_nexus": ["TradeDock Nexus Prime", "TradeDock Nexus Apex"],
}

# Default commodity scaffold matching the translator's _build_full_commodities.
# base_price derives from the WO-Y / ADR-0082 single source of truth
# (src.core.commodity_economy) — WO-ARCH-RES-2 dedup; capacity/production_rate/
# price_variance remain local bootstrap shape, not price econ.
COMMODITY_DEFAULTS = {
    "ore": {"capacity": 5000, "base_price": _commodity_base_price("ore"), "price_variance": 20, "production_rate": 100},
    "fuel": {"capacity": 4000, "base_price": _commodity_base_price("fuel"), "price_variance": 15, "production_rate": 120},
    "organics": {"capacity": 3000, "base_price": _commodity_base_price("organics"), "price_variance": 25, "production_rate": 80},
    "colonists": {"capacity": 500, "base_price": _commodity_base_price("colonists"), "price_variance": 10, "production_rate": 10},
    "equipment": {"capacity": 2000, "base_price": _commodity_base_price("equipment"), "price_variance": 30, "production_rate": 50},
    "gourmet_food": {"capacity": 600, "base_price": _commodity_base_price("gourmet_food"), "price_variance": 35, "production_rate": 15},
    "luxury_goods": {"capacity": 800, "base_price": _commodity_base_price("luxury_goods"), "price_variance": 40, "production_rate": 20},
    "precious_metals": {"capacity": 400, "base_price": _commodity_base_price("precious_metals"), "price_variance": 30, "production_rate": 8},
    "exotic_technology": {"capacity": 200, "base_price": _commodity_base_price("exotic_technology"), "price_variance": 50, "production_rate": 5},
}


def _fresh_commodities():
    return {
        name: {
            "buys": False,
            "sells": False,
            "quantity": 0,
            "current_price": cfg["base_price"],
            **cfg,
        }
        for name, cfg in COMMODITY_DEFAULTS.items()
    }


def repair(db) -> dict:
    stats = {"created": 0, "skipped_regions": 0}
    regions = db.query(Region).all()

    for region in regions:
        tiers = QUOTAS.get(region.region_type)
        if not tiers:
            continue

        existing = (
            db.query(Station)
            .filter(Station.region_id == region.id, Station.tradedock_tier.isnot(None))
            .count()
        )
        if existing:
            stats["skipped_regions"] += 1
            logger.info("%s already has %d TradeDock(s); skipping", region.name, existing)
            continue

        sectors = (
            db.query(Sector)
            .filter(Sector.region_id == region.id)
            .order_by(Sector.sector_id)
            .all()
        )
        if not sectors:
            continue
        base = sectors[0].sector_id  # region-local 1 == this global id
        total = len(sectors)
        occupied = {
            s for (s,) in db.query(Station.sector_id).filter(Station.region_id == region.id)
        }

        if region.region_type == "terran_space":
            pool = [s for s in sectors if base + 1 <= s.sector_id <= base + 98
                    and s.sector_id not in occupied]
        else:
            pool = [s for s in sectors if s.sector_id >= base + total // 2
                    and s.sector_id not in occupied]

        rng = random.Random(f"repair:{region.name}:tradedocks")
        counters = {"A": 0, "B": 0}
        for tier in tiers:
            if not pool:
                logger.warning("%s: no free sector for Tier-%s TradeDock", region.name, tier)
                continue
            sector = pool.pop(rng.randrange(len(pool)))
            if tier == "A":
                tier_names = TIER_A_NAMES_BY_REGION.get(region.region_type, NAMES["A"])
            else:
                tier_names = NAMES[tier]
            name = tier_names[counters[tier] % len(tier_names)]
            counters[tier] += 1

            commodities = apply_class_pattern(
                _fresh_commodities(),
                StationClass.CLASS_11,
                random.Random(f"repair:{sector.sector_id}:{name}"),
            )
            station = Station(
                name=name,
                sector_id=sector.sector_id,
                sector_uuid=sector.id,
                region_id=region.id,
                station_class=StationClass.CLASS_11,
                type=StationType.SHIPYARD,
                status=StationStatus.OPERATIONAL,
                commodities=commodities,
                services={
                    "ship_dealer": True, "ship_repair": True, "ship_maintenance": True,
                    "ship_upgrades": True, "insurance": True, "drone_shop": True,
                    "genesis_dealer": False, "mine_dealer": True,
                    "diplomatic_services": False, "storage_rental": True,
                    "market_intelligence": True, "refining_facility": True,
                    "luxury_amenities": tier == "A",
                },
                is_spacedock=False,
                tradedock_tier=tier,
                is_player_ownable=False,  # NPC-neutral infrastructure per canon
                description=(
                    "Tier-A TradeDock — Warp-Jumper-capable construction shipyard."
                    if tier == "A"
                    else "Tier-B TradeDock — standard construction shipyard."
                ),
            )
            db.add(station)
            db.flush()
            for row in build_market_prices(station.id, station.commodities):
                db.add(row)
            stats["created"] += 1
            logger.info("Seeded %s (Tier-%s) at sector %d in %s",
                        name, tier, sector.sector_id, region.name)

    db.commit()
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    session = SessionLocal()
    try:
        result = repair(session)
        print(f"TradeDock repair complete: {result}")
    finally:
        session.close()
