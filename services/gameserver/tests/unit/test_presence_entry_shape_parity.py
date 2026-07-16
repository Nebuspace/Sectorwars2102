"""QUEUE-HEAL-ENTRY-SHAPE (2026-07-16): live evidence -- a healed presence
entry had a correct ``ship_id`` but null ``ship_name``/``ship_type``, even
though the heal pass's own candidate query already had the ship join data
available. Root cause: two INDEPENDENT dict-literal constructions of the
same ``players_present`` entry shape -- movement_service._update_player_
presence (the organic arrival "reference shape") and presence_helpers'
heal pass -- had silently drifted (the heal path hardcoded ship_name/
ship_type to the string "None" instead of resolving them).

Fix: ONE shared constructor, ``intrasystem_movement_service.
build_presence_entry``, now used by BOTH call sites (movement_service.py's
own doc-comment at the call site references this same convergence). This
file proves the ACCEPT criterion directly: a healed entry and an
organically-arrived entry for the SAME player state are KEY-SET AND VALUE
equal.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from src.services.intrasystem_movement_service import build_presence_entry


def _movement_service_call(player, *, arrived_at):
    """Mirrors movement_service._update_player_presence's own call site
    verbatim (same argument-resolution expressions) -- NOT imported from
    there directly (that method also does Sector locking/JSONB RMW this
    test deliberately stays DB-free of), but the expressions themselves are
    copy-identical to what ships today, so this proves what the REFERENCE
    SHAPE actually resolves to for a given Player+Ship state."""
    return build_presence_entry(
        player_id=player.id,
        username=player.username,
        ship_id=player.current_ship_id,
        ship_name=player.current_ship.name if player.current_ship else None,
        ship_type=player.current_ship.type.name if player.current_ship else None,
        team_id=player.team_id,
        arrived_at=arrived_at,
    )


def _heal_pass_call(pid, username, ship_id, team_id, ship_name, ship_type_enum, *, arrived_at):
    """Mirrors _heal_missing_or_poseless_presence_sync's own call site
    verbatim -- the heal loop unpacks raw SQL-row tuples (not a loaded
    Player/Ship ORM instance), so ship_type needs its own `.name` resolve
    at the call site exactly like the real code does."""
    return build_presence_entry(
        player_id=pid,
        username=username,
        ship_id=ship_id,
        ship_name=ship_name,
        ship_type=ship_type_enum.name if ship_type_enum else None,
        team_id=team_id,
        arrived_at=arrived_at,
    )


class TestHealedEntryMatchesOrganicArrivalEntry:
    def test_key_set_and_value_equality_player_with_a_ship(self) -> None:
        """State X: a player piloting a real ship, on a team."""
        pid = uuid.uuid4()
        ship_id = uuid.uuid4()
        team_id = uuid.uuid4()
        frozen_now = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)

        ship = SimpleNamespace(name="Nomad", type=SimpleNamespace(name="LIGHT_FREIGHTER"))
        player = SimpleNamespace(
            id=pid, username="sweepclean", current_ship_id=ship_id,
            current_ship=ship, team_id=team_id,
        )
        organic = _movement_service_call(player, arrived_at=frozen_now)

        healed = _heal_pass_call(
            pid, "sweepclean", ship_id, team_id, "Nomad",
            SimpleNamespace(name="LIGHT_FREIGHTER"), arrived_at=frozen_now,
        )

        assert set(organic.keys()) == set(healed.keys())
        assert organic == healed
        # Sanity: the live bug's exact symptom must NOT reproduce.
        assert healed["ship_name"] != "None"
        assert healed["ship_type"] != "None"
        assert healed["ship_name"] == "Nomad"
        assert healed["ship_type"] == "LIGHT_FREIGHTER"

    def test_key_set_and_value_equality_player_with_no_ship(self) -> None:
        """State Y: a player with no current ship (e.g. mid-eject) -- the
        pre-existing "None" string fallback must still apply identically
        on both paths (this is NOT the bug; the bug was only when ship
        data DID exist and got dropped)."""
        pid = uuid.uuid4()
        team_id = uuid.uuid4()
        frozen_now = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)

        player = SimpleNamespace(
            id=pid, username="podless", current_ship_id=None,
            current_ship=None, team_id=team_id,
        )
        organic = _movement_service_call(player, arrived_at=frozen_now)

        healed = _heal_pass_call(
            pid, "podless", None, team_id, None, None, arrived_at=frozen_now,
        )

        assert set(organic.keys()) == set(healed.keys())
        assert organic == healed
        assert healed["ship_name"] == "None"
        assert healed["ship_type"] == "None"
        assert healed["ship_id"] is None

    def test_key_set_and_value_equality_no_team(self) -> None:
        """State Z: a ship but no team -- team_id None fallback parity."""
        pid = uuid.uuid4()
        ship_id = uuid.uuid4()
        frozen_now = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)

        ship = SimpleNamespace(name="Rustbucket", type=SimpleNamespace(name="ESCAPE_POD"))
        player = SimpleNamespace(
            id=pid, username="lonewolf", current_ship_id=ship_id,
            current_ship=ship, team_id=None,
        )
        organic = _movement_service_call(player, arrived_at=frozen_now)

        healed = _heal_pass_call(
            pid, "lonewolf", ship_id, None, "Rustbucket",
            SimpleNamespace(name="ESCAPE_POD"), arrived_at=frozen_now,
        )

        assert set(organic.keys()) == set(healed.keys())
        assert organic == healed
        assert healed["team_id"] is None
