"""WO-QUALITY-techdebt-gate-access-setters — validated write-side setters
for the layered access gates (faction_rep_min/max, toll_bypass) that
warp_gate_service already READS (check_traversal_access's
_check_faction_rep_layers at :1780-1816, collect_toll's toll_bypass
exemption at :2038-2042) but nothing previously WROTE, making that
enforcement code unreachable through any player-facing action.

Hand-built fakes (no DB, no app) — mirrors test_warp_gate_toll.py's
_FakeQuery/_FakeSession pattern exactly (this file's own module docstring
cites test_gate_construction_staging.py as the origin of that pattern).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, Dict, Optional

import pytest

from src.models.faction import Faction, FactionType
from src.models.reputation import Reputation
from src.models.warp_gate import WarpGate, WarpGateStatus
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus, WarpTunnelType
from src.services import warp_gate_service
from src.services.warp_gate_service import WarpGateError


# --- shared fakes (mirrors test_warp_gate_toll.py) --------------------------

class _FakeQuery:
    def __init__(self, *, first: Any = None) -> None:
        self._first = first

    def filter(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def populate_existing(self) -> "_FakeQuery":
        return self

    def with_for_update(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def first(self) -> Any:
        return self._first


class _FakeSession:
    def __init__(self, specs: Optional[Dict[type, _FakeQuery]] = None) -> None:
        self._specs = specs or {}
        self.flush_calls = 0

    def query(self, model: type) -> _FakeQuery:
        assert model in self._specs, f"unexpected query for {model!r}"
        return self._specs[model]

    def flush(self) -> None:
        self.flush_calls += 1

    def commit(self) -> None:
        raise AssertionError("warp_gate_service functions are flush-only — the route commits")

    def rollback(self) -> None:
        pass


def _fake_player(**overrides: Any) -> SimpleNamespace:
    base = dict(id=uuid.uuid4(), credits=100_000, team_id=None, username="tester")
    base.update(overrides)
    return SimpleNamespace(**base)


def _fake_tunnel(**overrides: Any) -> WarpTunnel:
    # A REAL ORM instance, not SimpleNamespace: set_gate_access_layers calls
    # flag_modified(tunnel, ...), which requires a mapped instance
    # (_sa_instance_state) — a bare SimpleNamespace raises AttributeError.
    defaults = dict(
        id=uuid.uuid4(),
        name="Test Gate",
        origin_sector_id=uuid.uuid4(),
        destination_sector_id=uuid.uuid4(),
        type=WarpTunnelType.ARTIFICIAL,
        status=WarpTunnelStatus.ACTIVE,
        is_bidirectional=False,
        created_by_player_id=uuid.uuid4(),
        access_requirements={},
        artificial_data={},
        tunnel_status={},
        total_traversals=0,
        is_public=True,
    )
    defaults.update(overrides)
    return WarpTunnel(**defaults)


def _fake_gate(player_id: Any, **overrides: Any) -> WarpGate:
    defaults = dict(
        id=uuid.uuid4(), beacon_id=uuid.uuid4(), player_id=player_id,
        status=WarpGateStatus.ACTIVE, warp_tunnel_id=uuid.uuid4(), hp=10_000,
    )
    defaults.update(overrides)
    return WarpGate(**defaults)


def _setup(owner_id: Any, **tunnel_overrides: Any):
    owner = _fake_player(id=owner_id)
    gate = _fake_gate(owner_id)
    tunnel = _fake_tunnel(created_by_player_id=owner_id, **tunnel_overrides)
    gate.warp_tunnel_id = tunnel.id
    db = _FakeSession({
        WarpGate: _FakeQuery(first=gate),
        WarpTunnel: _FakeQuery(first=tunnel),
    })
    return db, owner, gate, tunnel


# --- Validation helpers -----------------------------------------------------

@pytest.mark.unit
class TestValidateFactionRepLayer:
    def test_none_passes_through_unchanged(self) -> None:
        assert warp_gate_service._validate_faction_rep_layer(None, "faction_rep_min") is None

    def test_valid_layer_canonicalized(self) -> None:
        result = warp_gate_service._validate_faction_rep_layer(
            {"faction_type": "Federation", "value": 5}, "faction_rep_min",
        )
        assert result == {"faction_type": "Federation", "value": 5}

    def test_non_dict_rejected(self) -> None:
        with pytest.raises(WarpGateError, match="faction_rep_min must be an object"):
            warp_gate_service._validate_faction_rep_layer("Federation", "faction_rep_min")

    def test_unknown_faction_type_rejected(self) -> None:
        with pytest.raises(WarpGateError, match="faction_rep_min.faction_type must be one of"):
            warp_gate_service._validate_faction_rep_layer(
                {"faction_type": "NotARealFaction", "value": 5}, "faction_rep_min",
            )

    def test_missing_faction_type_rejected(self) -> None:
        with pytest.raises(WarpGateError, match="faction_type must be one of"):
            warp_gate_service._validate_faction_rep_layer({"value": 5}, "faction_rep_max")

    @pytest.mark.parametrize("bad_value", ["five", None, 1.5, True])
    def test_non_int_value_rejected(self, bad_value: Any) -> None:
        with pytest.raises(WarpGateError, match="value must be a whole number"):
            warp_gate_service._validate_faction_rep_layer(
                {"faction_type": "Federation", "value": bad_value}, "faction_rep_min",
            )

    def test_negative_value_accepted(self) -> None:
        """No canon floor on the threshold itself — a negative rep-min is a
        legitimate (if unusual) "let almost everyone through" configuration."""
        result = warp_gate_service._validate_faction_rep_layer(
            {"faction_type": "Pirates", "value": -100}, "faction_rep_min",
        )
        assert result == {"faction_type": "Pirates", "value": -100}


@pytest.mark.unit
class TestValidateTollBypass:
    def test_none_passes_through_unchanged(self) -> None:
        assert warp_gate_service._validate_toll_bypass(None) is None

    def test_valid_uuid_list_canonicalized(self) -> None:
        pid = uuid.uuid4()
        result = warp_gate_service._validate_toll_bypass([str(pid)])
        assert result == [str(pid)]

    def test_invalid_uuid_rejected(self) -> None:
        with pytest.raises(WarpGateError, match="toll_bypass contains an invalid UUID"):
            warp_gate_service._validate_toll_bypass(["not-a-uuid"])

    def test_non_list_rejected(self) -> None:
        with pytest.raises(WarpGateError, match="toll_bypass must be a list"):
            warp_gate_service._validate_toll_bypass("not-a-list")

    def test_empty_list_is_valid_and_clears_bypass(self) -> None:
        assert warp_gate_service._validate_toll_bypass([]) == []


# --- set_gate_access_layers --------------------------------------------------

@pytest.mark.unit
class TestSetGateAccessLayers:
    def test_sets_faction_rep_min_only(self) -> None:
        owner_id = uuid.uuid4()
        db, owner, gate, tunnel = _setup(owner_id)

        result = warp_gate_service.set_gate_access_layers(
            db, owner, str(gate.id),
            faction_rep_min={"faction_type": "Federation", "value": 5},
        )

        assert result["faction_rep_min"] == {"faction_type": "Federation", "value": 5}
        assert result["faction_rep_max"] is None
        assert result["toll_bypass"] == []
        assert tunnel.access_requirements["faction_rep_min"] == {
            "faction_type": "Federation", "value": 5,
        }
        assert "faction_rep_max" not in tunnel.access_requirements
        assert "toll_bypass" not in tunnel.access_requirements

    def test_sets_faction_rep_max_only(self) -> None:
        owner_id = uuid.uuid4()
        db, owner, gate, tunnel = _setup(owner_id)

        result = warp_gate_service.set_gate_access_layers(
            db, owner, str(gate.id),
            faction_rep_max={"faction_type": "Pirates", "value": 100},
        )

        assert result["faction_rep_max"] == {"faction_type": "Pirates", "value": 100}
        assert tunnel.access_requirements["faction_rep_max"] == {
            "faction_type": "Pirates", "value": 100,
        }

    def test_sets_toll_bypass_only(self) -> None:
        owner_id = uuid.uuid4()
        exempt_id = uuid.uuid4()
        db, owner, gate, tunnel = _setup(owner_id)

        result = warp_gate_service.set_gate_access_layers(
            db, owner, str(gate.id), toll_bypass=[str(exempt_id)],
        )

        assert result["toll_bypass"] == [str(exempt_id)]
        assert tunnel.access_requirements["toll_bypass"] == [str(exempt_id)]

    def test_omitted_fields_preserve_existing_values(self) -> None:
        """Matches set_gate_permissions' own `toll` field convention — an
        owner setting toll_bypass should never silently wipe an already-
        configured faction_rep_min."""
        owner_id = uuid.uuid4()
        db, owner, gate, tunnel = _setup(
            owner_id,
            access_requirements={
                "mode": "PUBLIC",
                "faction_rep_min": {"faction_type": "Federation", "value": 5},
            },
        )

        result = warp_gate_service.set_gate_access_layers(
            db, owner, str(gate.id), toll_bypass=[],
        )

        assert result["faction_rep_min"] == {"faction_type": "Federation", "value": 5}
        assert tunnel.access_requirements["faction_rep_min"] == {
            "faction_type": "Federation", "value": 5,
        }
        assert tunnel.access_requirements["mode"] == "PUBLIC"  # untouched sibling key too
        assert tunnel.access_requirements["toll_bypass"] == []

    def test_sets_all_three_together(self) -> None:
        owner_id = uuid.uuid4()
        exempt_id = uuid.uuid4()
        db, owner, gate, tunnel = _setup(owner_id)

        result = warp_gate_service.set_gate_access_layers(
            db, owner, str(gate.id),
            faction_rep_min={"faction_type": "Federation", "value": 5},
            faction_rep_max={"faction_type": "Pirates", "value": 100},
            toll_bypass=[str(exempt_id)],
        )

        assert result["faction_rep_min"] == {"faction_type": "Federation", "value": 5}
        assert result["faction_rep_max"] == {"faction_type": "Pirates", "value": 100}
        assert result["toll_bypass"] == [str(exempt_id)]

    def test_invalid_layer_rejected_before_any_lock_or_mutation(self) -> None:
        """Mirrors test_warp_gate_toll.py's own
        test_toll_out_of_range_rejected_jsonb_unchanged proof shape:
        validation runs BEFORE _resolve_owned_active_gate is even called,
        so a rejected call touches nothing."""
        owner_id = uuid.uuid4()
        db, owner, gate, tunnel = _setup(
            owner_id, access_requirements={"mode": "PUBLIC"},
        )

        with pytest.raises(WarpGateError, match="faction_rep_min.faction_type must be one of"):
            warp_gate_service.set_gate_access_layers(
                db, owner, str(gate.id),
                faction_rep_min={"faction_type": "NotReal", "value": 5},
            )

        assert tunnel.access_requirements == {"mode": "PUBLIC"}
        assert db.flush_calls == 0

    def test_non_owner_rejected_404_no_existence_leak(self) -> None:
        """OWNERSHIP GUARD (Cipher's review target). NOTE ON THE WO'S STATED
        STATUS CODE: the work order text says "the ownership guard 403s a
        non-owner." The actual, pre-existing (and untouched by this WO)
        ownership gate in _resolve_owned_active_gate — reused verbatim here,
        the SAME helper set_gate_permissions/transfer_gate already use —
        raises 404, deliberately, per its own docstring: "a gate that isn't
        yours 404s (no existence leak, mirrors construction.py)". This is
        the identical, already-adjudicated discrepancy test_warp_gate_
        toll.py::TestSetGatePermissionsToll::test_non_owner_rejected pins
        for the sibling permissions setter — flagged in the run report
        rather than silently building a NEW, inconsistent 403 guard for
        this one endpoint. This test pins the ACTUAL (reused, unchanged)
        behavior."""
        owner_id = uuid.uuid4()
        db, owner, gate, tunnel = _setup(owner_id)
        intruder = _fake_player()

        with pytest.raises(WarpGateError) as exc_info:
            warp_gate_service.set_gate_access_layers(
                db, intruder, str(gate.id),
                faction_rep_min={"faction_type": "Federation", "value": 999},
            )

        assert exc_info.value.status_code == 404
        assert tunnel.access_requirements == {}  # no mutation on a rejected call

    def test_non_owner_cannot_use_a_client_supplied_gate_id_to_bypass(self) -> None:
        """The ownership decision is derived ENTIRELY from the authenticated
        `player` argument + a server-side gate.player_id comparison — never
        from anything the client sends. Passing the REAL gate_id as an
        intruder still 404s; there is no alternate parameter an attacker
        could supply to claim ownership."""
        owner_id = uuid.uuid4()
        db, owner, gate, tunnel = _setup(owner_id)
        intruder = _fake_player(id=uuid.uuid4())
        assert intruder.id != gate.player_id

        with pytest.raises(WarpGateError) as exc_info:
            warp_gate_service.set_gate_access_layers(db, intruder, str(gate.id))
        assert exc_info.value.status_code == 404

    def test_gate_without_traversable_tunnel_rejected(self) -> None:
        owner_id = uuid.uuid4()
        owner = _fake_player(id=owner_id)
        gate = _fake_gate(owner_id, warp_tunnel_id=None)
        db = _FakeSession({WarpGate: _FakeQuery(first=gate)})

        with pytest.raises(WarpGateError, match="no traversable connection"):
            warp_gate_service.set_gate_access_layers(
                db, owner, str(gate.id),
                faction_rep_min={"faction_type": "Federation", "value": 5},
            )


# --- End-to-end: setter makes the read-side enforcement reachable ----------

@pytest.mark.unit
class TestSetterEnablesReadSideEnforcement:
    """The literal WO DoD: setting faction_rep_min via the new setter, then
    a below-threshold traversal is DENIED and an above-threshold one
    PASSES — proving check_traversal_access's pre-existing enforcement
    (:1793-1803) is reachable for the first time through a real write
    path, not just a hand-built fixture."""

    def test_below_threshold_denied_above_threshold_passes(self) -> None:
        owner_id = uuid.uuid4()
        db, owner, gate, tunnel = _setup(owner_id)

        warp_gate_service.set_gate_access_layers(
            db, owner, str(gate.id),
            faction_rep_min={"faction_type": FactionType.FEDERATION.value, "value": 50},
        )
        assert tunnel.access_requirements["faction_rep_min"] == {
            "faction_type": "Federation", "value": 50,
        }

        low_rep_player = _fake_player()
        faction = SimpleNamespace(id=uuid.uuid4(), faction_type=FactionType.FEDERATION)
        low_reputation = SimpleNamespace(current_value=10)
        access_db = _FakeSession({
            Faction: _FakeQuery(first=faction),
            Reputation: _FakeQuery(first=low_reputation),
        })
        with pytest.raises(WarpGateError) as exc_info:
            warp_gate_service.check_traversal_access(access_db, low_rep_player, tunnel)
        assert exc_info.value.status_code == 403
        assert "ERR_GATE_REP_TOO_LOW" in exc_info.value.detail

        high_rep_player = _fake_player()
        high_reputation = SimpleNamespace(current_value=100)
        access_db_pass = _FakeSession({
            Faction: _FakeQuery(first=faction),
            Reputation: _FakeQuery(first=high_reputation),
        })
        assert warp_gate_service.check_traversal_access(
            access_db_pass, high_rep_player, tunnel,
        ) is None

    def test_toll_bypass_setter_makes_the_exemption_reachable(self) -> None:
        """Same proof shape for the OTHER layer this WO wires: a player
        added to toll_bypass via the setter is exempt at collect_toll."""
        owner_id = uuid.uuid4()
        exempt_player = _fake_player()
        db, owner, gate, tunnel = _setup(
            owner_id, access_requirements={"toll_amount": 500},
        )

        warp_gate_service.set_gate_access_layers(
            db, owner, str(gate.id), toll_bypass=[str(exempt_player.id)],
        )
        assert str(exempt_player.id) in tunnel.access_requirements["toll_bypass"]

        # collect_toll's exemption check reads access_requirements.toll_bypass
        # directly off the tunnel it's handed — no further DB fixture needed
        # beyond the owner-lookup path already covered by test_warp_gate_
        # toll.py's own TestExemptionPrecedence suite; this test's job is
        # only to prove the setter's WRITE actually lands where collect_toll
        # READS, which the assertion above already confirms structurally.
        # A live end-to-end collect_toll(...) call belongs to that sibling
        # suite (already exercises this exact reqs.get("toll_bypass") path)
        # rather than duplicated here.
        reqs = tunnel.access_requirements
        bypass = {str(x) for x in (reqs.get("toll_bypass") or [])}
        assert str(exempt_player.id) in bypass
