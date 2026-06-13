"""
Celestial Composer Service

Generates a deterministic, display-oriented "star system" description for a
sector: star(s), nebula haze, asteroid belt, procedural filler bodies, and the
sector's REAL Planet/Station rows merged onto stable orbits.

DETERMINISM CONTRACT
====================
Identical sector -> identical response. All randomness flows from a SplitMix64
PRNG seeded by ``sector_id * SECTOR_SEED_SALT``. Per-body and per-station
sub-seeds are derived from that root seed (never from ``random``, time, or
iteration order of unordered collections). Real planets are merged at slots
derived from each planet's UUID integer, processed in sorted-by-id order, with
deterministic linear-probe collision handling.

INTERPRETATION NOTES (no canon exists for system composition — proposed canon):
- Star spectral distribution (relative weights):
    M_DWARF 30 / K_ORANGE 20 / G_YELLOW 15 / F_WHITE 10 / A_BLUE 8 /
    B_BLUE_GIANT 4 / O_BLUE_SUPER 1 / RED_GIANT 5 / WHITE_DWARF 4 / NEUTRON 2
  Binary modifier: 12% chance of a secondary star drawn from the same table.
- Sector-type overrides:
    BLACK_HOLE   -> star.kind = BLACK_HOLE (no binary)
    VOID         -> star = null, 0-2 rogue bodies
    STAR_CLUSTER -> primary star + 2-4 extra_stars
    NEBULA       -> normal star + nebula {hue 0-360, density 0.3-0.9}
- Asteroid belt: ASTEROID_FIELD sectors always; all other types ~15% chance.
- Procedural body kinds (PlanetType subset, weights):
    BARREN 30 / ICE 20 / VOLCANIC 12 / DESERT 13 / GAS_GIANT 25
  Positional bias: gas-giant weight scales by (0.3 + 1.4 * f) and rocky weights
  by (1.4 - 0.8 * f) where f = slot / (n - 1) is the normalized orbital
  position — gas giants drift outward, rocky worlds inward, mirroring real
  protoplanetary frost-line sorting.
- size_class: rocky 1-5 (weighted toward small), gas giants 3-5.
- rings: gas giants 40%, others 5%. moons: 0-3, weighted upward by size_class.
- Orbits: cumulative gap walk (0.15 + sum of jittered steps), normalized so the
  outermost body sits at <= 1.0 display radius. Monotonic by construction.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.models.planet import Planet
from src.models.sector import Sector
from src.models.station import Station

# ---------------------------------------------------------------------------
# SplitMix64 PRNG
# ---------------------------------------------------------------------------
# SplitMix64 (Steele, Lea & Flood 2014): a tiny, well-distributed 64-bit PRNG.
# state advances by the golden-gamma constant; output is the finalizer mix.
# Chosen because it is trivially seedable, has no shared global state, and the
# same seed always yields the same stream on every platform.

_MASK64 = (1 << 64) - 1
_GOLDEN_GAMMA = 0x9E3779B97F4A7C15

# Documented salt so seed derivation is explicit and stable.
SECTOR_SEED_SALT = 0x5EC7042102  # "SECTor 2102"
BODY_SEED_SALT = 0xB0D1E5
STATION_SEED_SALT = 0x57A710


class SplitMix64:
    """Deterministic 64-bit PRNG. Same seed -> same stream, always."""

    def __init__(self, seed: int) -> None:
        self._state = seed & _MASK64

    def next_u64(self) -> int:
        self._state = (self._state + _GOLDEN_GAMMA) & _MASK64
        z = self._state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK64
        return (z ^ (z >> 31)) & _MASK64

    def random(self) -> float:
        """Uniform float in [0, 1)."""
        return self.next_u64() / float(1 << 64)

    def uniform(self, lo: float, hi: float) -> float:
        return lo + (hi - lo) * self.random()

    def randint(self, lo: int, hi: int) -> int:
        """Uniform int in [lo, hi] inclusive."""
        if hi <= lo:
            return lo
        return lo + self.next_u64() % (hi - lo + 1)

    def weighted_choice(self, items: Sequence[Tuple[Any, float]]) -> Any:
        """Pick an item from (value, weight) pairs; weights are relative."""
        total = sum(w for _, w in items)
        roll = self.random() * total
        acc = 0.0
        for value, weight in items:
            acc += weight
            if roll < acc:
                return value
        return items[-1][0]


def _sub_seed(root_seed: int, salt: int, index: int) -> int:
    """Derive an independent per-entity sub-seed from the sector root seed."""
    return (root_seed ^ (salt * (index + 1)) ^ ((index + 1) << 17)) & _MASK64


# ---------------------------------------------------------------------------
# Star tables
# ---------------------------------------------------------------------------

# (kind, relative weight) — see module docstring for rationale.
STAR_WEIGHTS: List[Tuple[str, float]] = [
    ("M_DWARF", 30),
    ("K_ORANGE", 20),
    ("G_YELLOW", 15),
    ("F_WHITE", 10),
    ("A_BLUE", 8),
    ("B_BLUE_GIANT", 4),
    ("O_BLUE_SUPER", 1),
    ("RED_GIANT", 5),
    ("WHITE_DWARF", 4),
    ("NEUTRON", 2),
]

BINARY_CHANCE = 0.12

STAR_COLORS: Dict[str, str] = {
    "M_DWARF": "#ff6b4a",
    "K_ORANGE": "#ffa94d",
    "G_YELLOW": "#ffe066",
    "F_WHITE": "#fff4d6",
    "A_BLUE": "#cfe5ff",
    "B_BLUE_GIANT": "#9ec5ff",
    "O_BLUE_SUPER": "#7fb0ff",
    "RED_GIANT": "#ff4d4d",
    "WHITE_DWARF": "#e8f1ff",
    "NEUTRON": "#b39dff",
    "BLACK_HOLE": "#1a1026",
}

STAR_LABELS: Dict[str, str] = {
    "M_DWARF": "M-class Red Dwarf",
    "K_ORANGE": "K-class Orange Dwarf",
    "G_YELLOW": "G-class Yellow Star",
    "F_WHITE": "F-class White Star",
    "A_BLUE": "A-class Blue-White Star",
    "B_BLUE_GIANT": "B-class Blue Giant",
    "O_BLUE_SUPER": "O-class Blue Supergiant",
    "RED_GIANT": "Red Giant",
    "WHITE_DWARF": "White Dwarf",
    "NEUTRON": "Neutron Star",
    "BLACK_HOLE": "Black Hole",
}

# Habitable zone per star kind, expressed as (inner, outer) in the SAME
# normalized 0.15–1.0 display-orbit space the bodies use. Dimmer stars
# (M_DWARF, WHITE_DWARF) hold a tight HZ close in; hotter/brighter stars push
# it outward. Degenerate/dead stars have no HZ. Display-oriented, not real AU.
HZ_BANDS: Dict[str, Tuple[float, float]] = {
    "M_DWARF": (0.20, 0.34),
    "K_ORANGE": (0.28, 0.45),
    "G_YELLOW": (0.40, 0.62),
    "F_WHITE": (0.50, 0.74),
    "A_BLUE": (0.60, 0.85),
    "B_BLUE_GIANT": (0.70, 0.95),
    "O_BLUE_SUPER": (0.78, 1.0),
    "RED_GIANT": (0.55, 0.90),
    "WHITE_DWARF": (0.16, 0.26),
    # NEUTRON / BLACK_HOLE have no habitable zone.
}

# Planet kinds that count as habitable (must sit within the HZ).
HABITABLE_KINDS = {"TERRAN", "OCEANIC", "TROPICAL", "JUNGLE"}
HABITABLE_SCORE_MIN = 50


def _habitable_zone(star_kind: Optional[str]) -> Optional[Tuple[float, float]]:
    """The (inner, outer) habitable-zone band for a star kind, or None."""
    if not star_kind:
        return None
    return HZ_BANDS.get(star_kind)


def _is_habitable_planet(planet_type: str, habitability_score: Optional[int]) -> bool:
    if planet_type and planet_type.upper().replace("PLANETTYPE.", "") in HABITABLE_KINDS:
        return True
    return bool(habitability_score and habitability_score >= HABITABLE_SCORE_MIN)

# ---------------------------------------------------------------------------
# Procedural body tables
# ---------------------------------------------------------------------------

# Base relative weights (positionally rebalanced per slot — see _body_kind).
BODY_KIND_WEIGHTS: Dict[str, float] = {
    "BARREN": 30,
    "ICE": 20,
    "VOLCANIC": 12,
    "DESERT": 13,
    "GAS_GIANT": 25,
}

ROCKY_KINDS = ("BARREN", "ICE", "VOLCANIC", "DESERT")

BELT_CHANCE_DEFAULT = 0.15
# Chance a system carries a destroyed-planet debris field (two worlds that
# collided): pure cosmetic flourish, like the belt — not a click target, not
# persisted. Rolled LAST in generate_system so it never perturbs body layout.
DEBRIS_CHANCE = 0.12

MAX_BODIES = 9
MIN_ORBIT_AU = 0.15
MAX_ORBIT_AU = 1.0


def _make_star(rng: SplitMix64) -> Dict[str, Any]:
    kind = rng.weighted_choice(STAR_WEIGHTS)
    star: Dict[str, Any] = {
        "kind": kind,
        "label": STAR_LABELS[kind],
        "color": STAR_COLORS[kind],
    }
    if rng.random() < BINARY_CHANCE:
        secondary_kind = rng.weighted_choice(STAR_WEIGHTS)
        star["secondary"] = {
            "kind": secondary_kind,
            "color": STAR_COLORS[secondary_kind],
        }
    return star


def _body_kind(rng: SplitMix64, slot: int, total: int) -> str:
    """Pick a body kind with positional frost-line bias.

    f in [0, 1] is the normalized orbital position. Gas giants get heavier
    toward the outer system (x0.3 inner -> x1.7 outer); rocky kinds get
    heavier toward the inner system (x1.4 inner -> x0.6 outer).
    """
    f = slot / (total - 1) if total > 1 else 0.5
    weighted: List[Tuple[str, float]] = []
    for kind, base in BODY_KIND_WEIGHTS.items():
        if kind == "GAS_GIANT":
            weighted.append((kind, base * (0.3 + 1.4 * f)))
        else:
            weighted.append((kind, base * (1.4 - 0.8 * f)))
    return rng.weighted_choice(weighted)


def _make_body(root_seed: int, slot: int, total: int, orbit_au: float) -> Dict[str, Any]:
    rng = SplitMix64(_sub_seed(root_seed, BODY_SEED_SALT, slot))
    kind = _body_kind(rng, slot, total)

    if kind == "GAS_GIANT":
        size_class = rng.randint(3, 5)
        rings = rng.random() < 0.40
    else:
        # Rocky worlds skew small: weights 5/4/3/2/1 for sizes 1..5.
        size_class = rng.weighted_choice([(1, 5), (2, 4), (3, 3), (4, 2), (5, 1)])
        rings = rng.random() < 0.05

    # Moons 0-3, larger bodies hold more: weight for m moons = 1 + size bonus.
    moon_weights = [
        (0, 6 - size_class),
        (1, 3),
        (2, 1 + size_class * 0.5),
        (3, max(0.25, size_class - 2)),
    ]
    moons = rng.weighted_choice(moon_weights)

    return {
        "slot": slot,
        "orbit_au": round(orbit_au, 4),
        "kind": kind,
        "size_class": size_class,
        "palette": {
            "hue": rng.randint(0, 359),
            "sat": rng.randint(40, 90),
        },
        "rings": rings,
        "moons": moons,
        "phase_deg": rng.randint(0, 359),
        "real": False,
    }


def _make_orbits(rng: SplitMix64, count: int) -> List[float]:
    """Cumulative gap walk: monotonic orbits, normalized into display range.

    orbit[i] = orbit[i-1] + step * jitter (jitter in [0.7, 1.3]); the whole
    ladder is then rescaled so the outermost body lands at <= MAX_ORBIT_AU.
    """
    if count <= 0:
        return []
    step = (MAX_ORBIT_AU - MIN_ORBIT_AU) / count
    orbits: List[float] = []
    current = MIN_ORBIT_AU
    for _ in range(count):
        current += step * rng.uniform(0.7, 1.3)
        orbits.append(current)
    outermost = orbits[-1]
    if outermost > MAX_ORBIT_AU:
        scale = (MAX_ORBIT_AU - MIN_ORBIT_AU) / (outermost - MIN_ORBIT_AU)
        orbits = [MIN_ORBIT_AU + (o - MIN_ORBIT_AU) * scale for o in orbits]
    return orbits


def _planet_sort_key(planet: Planet) -> str:
    return str(planet.id)


def _merge_real_planets(
    bodies: List[Dict[str, Any]],
    planets: List[Planet],
    root_seed: int,
    hz: Optional[Tuple[float, float]] = None,
) -> None:
    """Merge real Planet rows into the procedural bodies, in place.

    Slot selection: per-planet PRNG seeded by the planet UUID's 128-bit int
    (folded to 64 bits) XOR the sector root seed -> slot = u64 % len(bodies).
    Planets are processed in sorted-by-id order, so slot assignment is stable.
    Collision handling: if the chosen slot already holds a real planet,
    linear-probe upward (slot + 1, + 2, ... mod len) until a procedural slot
    is found — deterministic because processing order is deterministic.

    Habitable placement: a habitable planet (TERRAN/OCEANIC/… or habitability
    >= 50) is steered to a free slot whose orbit lies within the star's
    habitable zone ``hz`` so green worlds only ever appear in the green band.
    Falls back to the hash slot when no HZ slot is free (or the star has none).
    """
    if not bodies or not planets:
        return
    taken: set = set()
    for planet in sorted(planets, key=_planet_sort_key):
        uuid_int = planet.id.int if hasattr(planet.id, "int") else int(str(planet.id).replace("-", ""), 16)
        folded = ((uuid_int >> 64) ^ uuid_int) & _MASK64
        slot_rng = SplitMix64(folded ^ root_seed)
        slot = slot_rng.next_u64() % len(bodies)
        # Linear probe past already-claimed slots.
        for _ in range(len(bodies)):
            if slot not in taken:
                break
            slot = (slot + 1) % len(bodies)
        else:
            # All slots hold real planets already (more real planets than
            # bodies should be prevented by the caller sizing bodies first).
            continue

        planet_type = planet.type.value if hasattr(planet.type, "value") else str(planet.type)
        # Steer habitable worlds into the habitable zone: if the hash slot's
        # orbit is outside the HZ, re-home to the nearest free in-HZ slot
        # (deterministic scan). Non-habitable planets keep the hash slot.
        if hz is not None and _is_habitable_planet(planet_type, planet.habitability_score):
            lo, hi = hz
            if not (lo <= bodies[slot]["orbit_au"] <= hi):
                hz_slots = sorted(
                    (i for i in range(len(bodies))
                     if i not in taken and lo <= bodies[i]["orbit_au"] <= hi),
                    key=lambda i: abs(bodies[i]["orbit_au"] - (lo + hi) / 2),
                )
                if hz_slots:
                    slot = hz_slots[0]
        taken.add(slot)

        body = bodies[slot]
        body["kind"] = planet_type
        body["real"] = True
        body["planet_id"] = str(planet.id)
        # ADR-0073: show the discoverer's custom name, else the auto-name, else
        # the legacy name; carry the discoverer so the route can gate renaming.
        body["name"] = planet.display_name
        body["discovered_by"] = str(planet.discovered_by) if planet.discovered_by else None
        body["habitability"] = planet.habitability_score
        body["owned"] = planet.owner_id is not None
        # Real gas giants keep the gas-giant size floor for visual sanity.
        if planet_type == "GAS_GIANT" and body["size_class"] < 3:
            body["size_class"] = 3


def _make_stations(stations: List[Station], root_seed: int) -> List[Dict[str, Any]]:
    """Real Station rows on stable seeded orbits (per-station-id sub-seed)."""
    results: List[Dict[str, Any]] = []
    for station in sorted(stations, key=lambda s: str(s.id)):
        uuid_int = station.id.int if hasattr(station.id, "int") else int(str(station.id).replace("-", ""), 16)
        folded = ((uuid_int >> 64) ^ uuid_int) & _MASK64
        rng = SplitMix64((folded ^ root_seed ^ STATION_SEED_SALT) & _MASK64)
        results.append({
            "station_id": str(station.id),
            "name": station.name,
            "type": station.type.value if hasattr(station.type, "value") else str(station.type),
            "orbit_au": round(rng.uniform(0.2, 0.95), 4),
            "phase_deg": rng.randint(0, 359),
        })
    return results


def generate_skeleton(sector: Sector, min_bodies: int = 0) -> Dict[str, Any]:
    """The deterministic procedural SKELETON of a sector's system — everything
    except real Planet/Station rows: star(s), nebula, asteroid belt, body
    skeleton, collision-debris ring, habitable zone. Persisted once
    (SectorCelestial) then read back; real planets/stations are merged over it
    at request time. ``min_bodies`` (the real-planet count at first generation)
    guarantees the skeleton has enough body slots for them."""
    sector_type = sector.type.value if hasattr(sector.type, "value") else str(sector.type)
    root_seed = (sector.sector_id * SECTOR_SEED_SALT) & _MASK64
    rng = SplitMix64(root_seed)

    # --- Star(s): consume rolls in a FIXED order so determinism holds. -----
    star: Optional[Dict[str, Any]] = None
    extra_stars: Optional[List[Dict[str, Any]]] = None
    nebula: Optional[Dict[str, Any]] = None

    if sector_type == "BLACK_HOLE":
        star = {
            "kind": "BLACK_HOLE",
            "label": STAR_LABELS["BLACK_HOLE"],
            "color": STAR_COLORS["BLACK_HOLE"],
        }
    elif sector_type == "VOID":
        star = None
    elif sector_type == "STAR_CLUSTER":
        star = _make_star(rng)
        extra_stars = []
        for _ in range(rng.randint(2, 4)):
            kind = rng.weighted_choice(STAR_WEIGHTS)
            extra_stars.append({"kind": kind, "color": STAR_COLORS[kind]})
    else:
        star = _make_star(rng)
        if sector_type == "NEBULA":
            nebula = {
                "hue": rng.randint(0, 360),
                "density": round(rng.uniform(0.3, 0.9), 3),
            }

    # --- Asteroid belt -----------------------------------------------------
    belt: Optional[Dict[str, Any]] = None
    if sector_type == "ASTEROID_FIELD" or rng.random() < BELT_CHANCE_DEFAULT:
        inner = rng.uniform(0.3, 0.6)
        belt = {
            "inner_au": round(inner, 4),
            "outer_au": round(inner + rng.uniform(0.08, 0.2), 4),
        }

    # --- Bodies (procedural skeleton; real planets merged later) -----------
    if sector_type == "VOID":
        body_count = rng.randint(0, 2)  # rogue bodies adrift in the dark
    else:
        body_count = rng.randint(1, 7)
    # Real planets need homes; grow the skeleton to fit them (cap at 9).
    body_count = min(MAX_BODIES, max(body_count, min_bodies))

    # Habitable zone for this star (None for dead/degenerate stars).
    hz = _habitable_zone(star.get("kind") if star else None)

    orbits = _make_orbits(rng, body_count)
    bodies = [
        _make_body(root_seed, slot, body_count, orbit_au)
        for slot, orbit_au in enumerate(orbits)
    ]
    # Composer-only bodies get a real name from the corpus (replacing the old
    # "<sector>-<letter>" designation); real planets overwrite this at merge.
    # Deferred import avoids a module-load cycle (planet_naming -> celestial).
    from src.services.planet_naming_service import name_for_body
    for b in bodies:
        b["name"] = name_for_body(sector.sector_id, b["slot"])

    # --- Collision-debris ring (two worlds that collided long ago, their wreck
    #     spread into a belt encircling the orbital plane) — rolled LAST so
    #     adding it leaves every existing system's star/belt/body layout
    #     untouched. An annulus like the asteroid belt, not a single cluster. ---
    debris: Optional[Dict[str, Any]] = None
    if star is not None and body_count > 0 and rng.random() < DEBRIS_CHANCE:
        inner = rng.uniform(0.3, 0.7)
        debris = {
            "inner_au": round(inner, 4),
            "outer_au": round(inner + rng.uniform(0.1, 0.25), 4),
            # Warm rocky tones (rust/ember) so the collision wreck reads as rock
            # and never blends with the green habitable-zone band.
            "hue": rng.randint(8, 38),
        }

    habitable_zone = {"inner_au": round(hz[0], 4), "outer_au": round(hz[1], 4)} if hz else None

    skeleton: Dict[str, Any] = {
        "sector_type": sector_type,
        "seed": root_seed,
        "star": star,
        "extra_stars": extra_stars,
        "nebula": nebula,
        "belt": belt,
        "debris": debris,
        "habitable_zone": habitable_zone,
        "bodies": bodies,
    }
    return skeleton


def get_or_create_celestial(db, sector: Sector, min_bodies: int = 0) -> Dict[str, Any]:
    """Read the persisted procedural skeleton for a sector, generating + storing
    it on first visit. Race-safe (INSERT ... ON CONFLICT DO NOTHING, then
    re-select) so concurrent first-visits don't double-insert. Returns the
    composition dict (the skeleton)."""
    from src.models.sector_celestial import SectorCelestial
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    row = (
        db.query(SectorCelestial)
        .filter(SectorCelestial.sector_uuid == sector.id)
        .first()
    )
    if row is not None:
        return row.composition

    comp = generate_skeleton(sector, min_bodies=min_bodies)
    stmt = (
        pg_insert(SectorCelestial.__table__)
        .values(
            sector_uuid=sector.id,
            sector_id=sector.sector_id,
            composition=comp,
            seed=int(comp["seed"]),
        )
        .on_conflict_do_nothing(index_elements=["sector_uuid"])
    )
    db.execute(stmt)
    db.flush()
    row = (
        db.query(SectorCelestial)
        .filter(SectorCelestial.sector_uuid == sector.id)
        .first()
    )
    return row.composition if row is not None else comp


def generate_system(
    db,
    sector: Sector,
    planets: List[Planet],
    stations: List[Station],
) -> Dict[str, Any]:
    """Compose the full system description for a sector: the persisted procedural
    skeleton (star/belt/nebula/debris/HZ/body slots) with the real Planet and
    Station rows merged over it. Response shape is unchanged from the legacy
    per-request generator."""
    import copy

    real_planets = sorted(planets, key=_planet_sort_key)[:MAX_BODIES]
    comp = get_or_create_celestial(db, sector, min_bodies=len(real_planets))

    root_seed = int(comp.get("seed") or ((sector.sector_id * SECTOR_SEED_SALT) & _MASK64))
    # Deep-copy the skeleton bodies before merging — _merge_real_planets mutates
    # in place and the persisted skeleton must stay pristine.
    bodies = copy.deepcopy(comp.get("bodies") or [])
    # Ensure every composer body has a corpus name (older persisted skeletons
    # predate composer naming); real planets overwrite this at merge.
    from src.services.planet_naming_service import name_for_body
    for b in bodies:
        if not b.get("name"):
            b["name"] = name_for_body(sector.sector_id, b.get("slot", 0))
    hz_dict = comp.get("habitable_zone")
    hz = (hz_dict["inner_au"], hz_dict["outer_au"]) if hz_dict else None
    _merge_real_planets(bodies, real_planets, root_seed, hz)

    response: Dict[str, Any] = {
        "sector_id": sector.sector_id,
        "sector_type": comp.get("sector_type"),
        "star": comp.get("star"),
        "nebula": comp.get("nebula"),
        "belt": comp.get("belt"),
        "debris": comp.get("debris"),
        "habitable_zone": hz_dict,
        "bodies": bodies,
        "stations": _make_stations(stations, root_seed),
    }
    if comp.get("extra_stars"):
        response["extra_stars"] = comp["extra_stars"]
    return response
