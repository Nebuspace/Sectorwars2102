"""Unit tests for MovementService._check_for_encounters' faction-patrol
leg (WO-RT-PATROL-ENCOUNTER) and Player.is_wanted_at.

Two canon-vs-shipped-code conflicts were found and resolved here rather
than built blind -- see the code comments at the call sites for the full
evidence trail:

1. Data shape. sector-presence.md's pseudocode reads
   ``sector.defenses['patrol_ships']`` as a list of squad dicts. That key
   is already a live SCALAR INT elsewhere (station siege-defense fire
   power in combat_service.py, admin's security_level rollup,
   MILITARY_ZONE seeding in nexus_generation_service.py /
   bang_import_service.py -- the latter's own comment: "patrol_ships MUST
   be a SCALAR INT, never a [list]"). The already-shipped
   npc_spawn_service.py hit this exact conflict first and deliberately
   uses a SEPARATE key, ``police_patrol_ships``
   (POLICE_PATROL_DEFENSES_KEY), flagging the divergence rather than
   overloading patrol_ships. This leg follows that precedent.

2. Combat initiation. The doc's pseudocode calls
   "combat_resolver.attack_player(patrol, player) directly" as
   non-optional NPC-initiated combat. combat_service.py has no entry
   point shaped for an NPC-as-attacker (attack_player needs two real
   Player rows with a current_ship). The already-shipped v1 scope for
   these same squads explicitly defers this: npc_spawn_service.py's
   module docstring says v1 has "no NPC-initiated combat", and
   npc_engagement_service.py's docstring says "combat with the arrived
   squad is player-initiated PvE via the existing attack path". So this
   leg surfaces detection only (an informational encounter entry, like
   the players/hazard/drones legs below it) and does not call
   combat_service -- PARKED, flagged to the orchestrator, not silently
   invented.

Mock-session style mirrors test_movement_drone_encounters.py /
test_movement_region_sync.py: a MagicMock stands in for the SQLAlchemy
session; the Sector query is driven the usual way; no live DB required.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.models.faction import Faction
from src.models.player import Player
from src.models.reputation import Reputation
from src.models.sector import Sector
from src.services.movement_service import MovementService


def make_player(player_id=None, is_wanted_at_result=False, is_wanted_at=None):
    """Lightweight stand-in for _check_for_encounters callers -- mirrors
    the drone-encounter test family's make_player(), extended with a
    stubbed is_wanted_at (Player.is_wanted_at itself is unit-tested
    separately below against the real ORM method). Pass an explicit
    ``is_wanted_at`` callable to exercise call-site behavior (e.g. a
    stub that raises, to prove the caller's try/except)."""
    return SimpleNamespace(
        id=player_id or uuid.uuid4(),
        is_wanted_at=is_wanted_at or (lambda faction_code, threshold: is_wanted_at_result),
    )


def make_sector(sector_uuid=None, sector_num=1301, defenses=None):
    return SimpleNamespace(
        id=sector_uuid or uuid.uuid4(),
        sector_id=sector_num,
        players_present=[],
        type=SimpleNamespace(name="STANDARD"),
        hazard_level=0,
        defenses=defenses,
    )


def build_service(sector, drones=()):
    """MovementService over a mock session: Sector query returns the
    fixed destination; Drone query returns an empty live-count (no
    hostile drones in these fixtures, so the drones leg stays quiet)."""
    from src.models.drone import Drone

    mock_db = MagicMock()

    def query_side_effect(model):
        if model is Sector:
            q = MagicMock()
            q.filter.return_value.first.return_value = sector
            return q
        if model is Drone:
            q = MagicMock()
            q.filter.return_value.count.return_value = len(drones)
            return q
        raise AssertionError(f"unexpected query target: {model!r}")

    mock_db.query.side_effect = query_side_effect
    return MovementService(mock_db)


def patrol_encounter(encounters):
    return next((e for e in encounters if e["type"] == "faction_patrol"), None)


def police_squad(faction_code="terran_federation", squad_kind="federation_marshal",
                  wanted_threshold=-500, ship_count=2):
    return {
        "patrol_id": str(uuid.uuid4()),
        "faction_code": faction_code,
        "squad_kind": squad_kind,
        "npc_character_ids": [str(uuid.uuid4()), str(uuid.uuid4())],
        "ship_count": ship_count,
        "wanted_threshold": wanted_threshold,
        "deployed_at": "2026-07-01T00:00:00Z",
    }


class TestFactionPatrolEncounter:
    def test_wanted_player_matching_patrol_yields_pursuit_encounter(self):
        player = make_player(is_wanted_at_result=True)
        sector = make_sector(defenses={"police_patrol_ships": [police_squad()]})
        service = build_service(sector)

        encounters = service._check_for_encounters(player, sector.sector_id)

        assert patrol_encounter(encounters) == {
            "type": "faction_patrol",
            "faction": "terran_federation",
            "squad": "federation_marshal",
            "ship_count": 2,
            "threat_level": "high",
            "engagement": "pursuit",
        }

    def test_clean_player_passes_untouched(self):
        """A player who doesn't clear is_wanted_at sees no patrol
        encounter even though a squad is present."""
        player = make_player(is_wanted_at_result=False)
        sector = make_sector(defenses={"police_patrol_ships": [police_squad()]})
        service = build_service(sector)

        encounters = service._check_for_encounters(player, sector.sector_id)

        assert patrol_encounter(encounters) is None

    def test_empty_patrol_list_yields_no_encounter(self):
        player = make_player(is_wanted_at_result=True)
        sector = make_sector(defenses={"police_patrol_ships": []})
        service = build_service(sector)

        encounters = service._check_for_encounters(player, sector.sector_id)

        assert patrol_encounter(encounters) is None

    def test_absent_defenses_key_yields_no_encounter(self):
        """No police_patrol_ships key at all (the common case -- most
        sectors carry no squad) -- no crash, no encounter."""
        player = make_player(is_wanted_at_result=True)
        sector = make_sector(defenses={"some_other_key": 1})
        service = build_service(sector)

        encounters = service._check_for_encounters(player, sector.sector_id)

        assert patrol_encounter(encounters) is None

    def test_defenses_none_yields_no_encounter(self):
        player = make_player(is_wanted_at_result=True)
        sector = make_sector(defenses=None)
        service = build_service(sector)

        encounters = service._check_for_encounters(player, sector.sector_id)

        assert patrol_encounter(encounters) is None

    def test_scalar_shaped_key_never_raises(self):
        """Regression pin: patrol_ships is a live scalar int elsewhere in
        this codebase (station siege-defense). If a caller or a future
        migration ever lands a bare int under police_patrol_ships, the
        tolerant reader must skip it, not crash -- this leg reads a
        dedicated key so real seeded data never hits this shape today,
        but the guard is load-bearing defense-in-depth."""
        player = make_player(is_wanted_at_result=True)
        sector = make_sector(defenses={"police_patrol_ships": 3})
        service = build_service(sector)

        encounters = service._check_for_encounters(player, sector.sector_id)

        assert patrol_encounter(encounters) is None

    def test_malformed_patrol_entries_never_raise(self):
        """A garbage entry (not a dict) and a dict missing wanted_threshold
        are skipped before is_wanted_at is ever called; a dict with a
        non-numeric threshold reaches is_wanted_at, which raises for it
        (mirroring the real Player.is_wanted_at's int(threshold) coercion)
        -- proving the call site's try/except swallows it rather than
        propagating. A valid entry alongside all three still matches."""
        def strict_is_wanted_at(faction_code, threshold):
            int(threshold)  # raises ValueError for "not-a-number"
            return True

        player = make_player(is_wanted_at=strict_is_wanted_at)
        sector = make_sector(defenses={"police_patrol_ships": [
            "not-a-dict",
            {"faction_code": "pirates"},  # missing wanted_threshold
            {"faction_code": "pirates", "wanted_threshold": "not-a-number"},
            police_squad(faction_code="galactic_concord", squad_kind="nexus_sentinel"),
        ]})
        service = build_service(sector)

        encounters = service._check_for_encounters(player, sector.sector_id)

        matched = [e for e in encounters if e["type"] == "faction_patrol"]
        assert len(matched) == 1
        assert matched[0]["faction"] == "galactic_concord"

    def test_multiple_matching_squads_yield_multiple_encounters(self):
        player = make_player(is_wanted_at_result=True)
        sector = make_sector(defenses={"police_patrol_ships": [
            police_squad(faction_code="terran_federation", squad_kind="federation_marshal"),
            police_squad(faction_code="galactic_concord", squad_kind="nexus_sentinel"),
        ]})
        service = build_service(sector)

        encounters = service._check_for_encounters(player, sector.sector_id)

        matched = [e for e in encounters if e["type"] == "faction_patrol"]
        assert len(matched) == 2

    def test_no_combat_service_invoked(self):
        """Detection-only pin: this leg must never reach into
        combat_service -- movement_service.py imports no combat_service
        symbol, and _check_for_encounters takes no combat-service
        dependency. A wanted-match run must not raise or attempt any
        such call (there is nothing to mock/assert against because the
        call genuinely doesn't exist -- see the PARKED note above)."""
        import src.services.movement_service as movement_service_module

        assert not hasattr(movement_service_module, "combat_service")
        assert not hasattr(movement_service_module, "CombatService")

        player = make_player(is_wanted_at_result=True)
        sector = make_sector(defenses={"police_patrol_ships": [police_squad()]})
        service = build_service(sector)

        # Must complete without raising.
        service._check_for_encounters(player, sector.sector_id)


class TestSiblingLegsUnchanged:
    """Regression pins: the players/hazard/drones legs stay byte-identical
    for their fixtures now that the patrol leg sits after them."""

    def test_other_players_leg_unchanged(self):
        other_entry = {"player_id": str(uuid.uuid4()), "username": "Rival"}
        player = make_player()
        sector = make_sector()
        sector.players_present = [other_entry, {"player_id": str(player.id)}]
        service = build_service(sector)

        encounters = service._check_for_encounters(player, sector.sector_id)

        players_encounter = next(e for e in encounters if e["type"] == "players")
        assert players_encounter == {
            "type": "players",
            "players": [other_entry],
            "threat_level": "varies",
        }

    def test_sector_hazard_leg_unchanged(self):
        player = make_player()
        sector = make_sector()
        sector.type = SimpleNamespace(name="NEBULA")
        sector.hazard_level = 8
        service = build_service(sector)

        encounters = service._check_for_encounters(player, sector.sector_id)

        hazard_encounter = next(e for e in encounters if e["type"] == "sector_hazard")
        assert hazard_encounter == {
            "type": "sector_hazard",
            "hazard": "NEBULA",
            "threat_level": "high",
        }

    def test_drones_leg_unchanged(self):
        player = make_player()
        sector = make_sector()
        service = build_service(sector, drones=[object(), object(), object()])

        encounters = service._check_for_encounters(player, sector.sector_id)

        drones_encounter = next(e for e in encounters if e["type"] == "drones")
        assert drones_encounter == {
            "type": "drones",
            "count": 3,
            "threat_level": "low",
        }


class TestPlayerIsWantedAt:
    """Direct unit tests of the real Player.is_wanted_at implementation
    against transient (never-flushed, no session) ORM instances -- a
    SQLAlchemy declarative object's relationship collections default to
    an empty in-memory list and support plain in-memory appends without
    any DB, matching this codebase's established real-ORM-object,
    no-session unit-test pattern."""

    def test_is_wanted_flag_alone_is_sufficient(self):
        player = Player(is_wanted=True, is_suspect=False, personal_reputation=0)
        assert player.is_wanted_at("terran_federation", -500) is True

    def test_is_suspect_flag_alone_is_sufficient(self):
        player = Player(is_wanted=False, is_suspect=True, personal_reputation=0)
        assert player.is_wanted_at("terran_federation", -500) is True

    def test_personal_reputation_at_or_below_threshold_matches(self):
        player = Player(is_wanted=False, is_suspect=False, personal_reputation=-500)
        assert player.is_wanted_at("terran_federation", -500) is True

    def test_personal_reputation_above_threshold_does_not_match_alone(self):
        player = Player(is_wanted=False, is_suspect=False, personal_reputation=-100)
        assert player.is_wanted_at("terran_federation", -500) is False

    def test_faction_standing_at_or_below_threshold_matches(self):
        player = Player(is_wanted=False, is_suspect=False, personal_reputation=0)
        rep = Reputation(current_value=-30)
        rep.faction = Faction(name="pirates")
        player.faction_reputations.append(rep)

        assert player.is_wanted_at("pirates", -25) is True

    def test_faction_standing_above_threshold_does_not_match(self):
        player = Player(is_wanted=False, is_suspect=False, personal_reputation=0)
        rep = Reputation(current_value=10)
        rep.faction = Faction(name="pirates")
        player.faction_reputations.append(rep)

        assert player.is_wanted_at("pirates", -25) is False

    def test_no_matching_faction_reputation_row_falls_through_to_false(self):
        player = Player(is_wanted=False, is_suspect=False, personal_reputation=0)
        rep = Reputation(current_value=-999)
        rep.faction = Faction(name="pirates")
        player.faction_reputations.append(rep)

        # Player has terrible standing with pirates but the patrol is
        # terran_federation -- no match, no raise.
        assert player.is_wanted_at("terran_federation", -500) is False

    def test_unresolvable_threshold_never_raises(self):
        player = Player(is_wanted=False, is_suspect=False, personal_reputation=-999)
        assert player.is_wanted_at("terran_federation", "not-a-number") is False

    def test_missing_faction_code_never_raises(self):
        player = Player(is_wanted=False, is_suspect=False, personal_reputation=0)
        assert player.is_wanted_at(None, -500) is False
