"""WO-NEON-RES-NH16 — admin GET /admin/colonies must surface the real
Planet.morale field instead of leaving the client to fake it from
habitability_score.

Before this fix, the serializer in ``routes/admin.py::get_all_colonies``
omitted ``morale`` entirely, forcing the admin-ui to derive a fake "morale"
from ``habitability_score || 50`` — which floors a genuine zero-habitability
colony up to a fake 50 and never reflects the real siege-morale field
(``Planet.morale``, Integer NOT NULL default 100, with an active siege
mechanic that can drive it to 0).

No real DB: a tiny in-memory fake Session routes ``query(Planet).all()`` to a
seeded list of SimpleNamespace stand-ins (mirroring the pattern in
test_bounty_service_nh2.py / test_research_service.py), and
``get_all_colonies`` is called directly — Depends() defaults are just
parameter defaults, so passing real args bypasses FastAPI's DI entirely.
"""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

from src.api.routes.admin import get_all_colonies


def make_planet(*, habitability_score=0, resource_richness=1.0, morale=100, name="P"):
    """A Planet stand-in carrying exactly the attributes the serializer
    reads (direct access for the fixed fields, getattr-with-default for the
    optional ones) — no SQLAlchemy mapping required."""
    return SimpleNamespace(
        id=uuid4(),
        name=name,
        sector_id=1,
        type=None,
        status=None,
        owner_id=None,
        population=0,
        max_population=0,
        habitability_score=habitability_score,
        resource_richness=resource_richness,
        morale=morale,
        defense_level=0,
        colonized_at=None,
        fuel_ore=0,
        organics=0,
        equipment=0,
        fighters=0,
        factory_level=0,
        farm_level=0,
        mine_level=0,
        research_level=0,
        under_siege=False,
        siege_attacker_id=None,
    )


class _FakeQuery:
    def __init__(self, planets):
        self._planets = planets

    def all(self):
        return self._planets


class _FakeSession:
    """Keyed list of Planet stand-ins; ``query(Planet).all()`` is the only
    call get_all_colonies makes when every planet is owner_id=None (the
    owner-lookup branch, a separate db.query(Player) call, is skipped)."""

    def __init__(self, planets):
        self._planets = planets

    def query(self, model):
        return _FakeQuery(self._planets)


def _run(planets):
    db = _FakeSession(planets)
    result = asyncio.run(get_all_colonies(current_admin=SimpleNamespace(), db=db))
    return result["colonies"]


def test_serializer_emits_integer_morale_mirroring_the_planet_row():
    zero_morale = make_planet(habitability_score=0, morale=0, name="zero-morale")
    full_morale = make_planet(habitability_score=0, morale=100, name="full-morale-zero-hab")
    zero_richness = make_planet(resource_richness=0, name="zero-richness")

    colonies = _run([zero_morale, full_morale, zero_richness])
    assert len(colonies) == 3

    by_name = {c["name"]: c for c in colonies}

    zm = by_name["zero-morale"]
    assert zm["morale"] == 0
    assert isinstance(zm["morale"], int)
    assert zm["habitability_score"] == 0

    fm = by_name["full-morale-zero-hab"]
    assert fm["morale"] == 100
    assert fm["habitability_score"] == 0

    zr = by_name["zero-richness"]
    assert zr["resource_richness"] == 0
    # morale/habitability passthrough is untouched by the richness case
    assert zr["morale"] == 100
    assert zr["habitability_score"] == 0


def test_a_zero_morale_planet_reads_zero_not_the_old_fifty_floor():
    """The exploit this WO closes: pre-fix the key was simply absent, and the
    admin-ui's own `|| 50` mask (fixed separately in the ui lane) would have
    floored this up to 50. The serializer's job is just to stop hiding the
    real value."""
    planet = make_planet(habitability_score=0, morale=0)
    colony = _run([planet])[0]
    assert colony["morale"] == 0
    assert colony["morale"] != 50
