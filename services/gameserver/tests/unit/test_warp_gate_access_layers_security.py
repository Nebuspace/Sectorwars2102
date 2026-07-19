"""Cipher security audit — WO-QUALITY-techdebt-gate-access-setters
(set_gate_access_layers / _validate_faction_rep_layer / _validate_toll_bypass).

Attacker-facing proofs only (ownership bypass / IDOR, 404-vs-403 existence
leak, input-abuse). Mirrors test_warp_gate_toll.py's _FakeQuery/_FakeSession
pattern and TestSetGatePermissionsToll's fixtures so the new PATCH
/{gate_id}/access-requirements route's ownership guard is proven against the
SAME harness as its set_gate_permissions sibling.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from src.models.faction import FactionType
from src.models.warp_gate import WarpGate, WarpGateStatus
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus, WarpTunnelType
from src.services import warp_gate_service
from src.services.warp_gate_service import WarpGateError


# --- shared fakes (verbatim pattern from test_warp_gate_toll.py) ------------


class _FakeQuery:
    def __init__(self, *, first: Any = None, count: int = 0) -> None:
        self._first = first
        self._count = count

    def filter(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def join(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def populate_existing(self) -> "_FakeQuery":
        return self

    def with_for_update(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def first(self) -> Any:
        return self._first

    def count(self) -> int:
        return self._count


class _FakeSession:
    def __init__(self, specs: Optional[Dict[type, _FakeQuery]] = None) -> None:
        self._specs = specs or {}
        self.added: List[Any] = []
        self.flush_calls = 0

    def query(self, model: type) -> _FakeQuery:
        assert model in self._specs, f"unexpected query for {model!r}"
        return self._specs[model]

    def add(self, obj: Any) -> None:
        self.added.append(obj)

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
        id=uuid.uuid4(),
        beacon_id=uuid.uuid4(),
        player_id=player_id,
        status=WarpGateStatus.ACTIVE,
        warp_tunnel_id=uuid.uuid4(),
        hp=10_000,
    )
    defaults.update(overrides)
    return WarpGate(**defaults)


def _setup(owner_id: Any, **tunnel_overrides: Any):
    owner = _fake_player(id=owner_id)
    gate = _fake_gate(owner_id)
    tunnel = _fake_tunnel(created_by_player_id=owner_id, **tunnel_overrides)
    gate.warp_tunnel_id = tunnel.id
    db = _FakeSession({WarpGate: _FakeQuery(first=gate), WarpTunnel: _FakeQuery(first=tunnel)})
    return db, owner, gate, tunnel


# --- Attack goal #1: ownership bypass / IDOR ---------------------------------


@pytest.mark.unit
class TestOwnershipGuard:
    def test_non_owner_cannot_set_access_layers_on_someone_elses_gate(self) -> None:
        """An authenticated intruder (a real, valid player — NOT the gate
        owner) targets another player's gate_id directly. The ownership
        guard must reject before any mutation, regardless of what the
        intruder puts in the body."""
        owner_id = uuid.uuid4()
        db, owner, gate, tunnel = _setup(owner_id)
        intruder = _fake_player()  # distinct random id — never == owner_id

        with pytest.raises(WarpGateError) as exc_info:
            warp_gate_service.set_gate_access_layers(
                db, intruder, str(gate.id),
                faction_rep_min={"faction_type": "Federation", "value": 999},
                faction_rep_max=None,
                toll_bypass=[str(intruder.id)],  # attacker tries to whitelist themselves
            )

        assert exc_info.value.status_code == 404
        # Zero mutation on the victim's gate — not even a partial write.
        assert tunnel.access_requirements == {}

    def test_gate_id_is_never_taken_from_request_body(self) -> None:
        """set_gate_access_layers' signature has no owner/player_id
        parameter at all — the only identity that can ever reach
        _resolve_owned_active_gate's ownership comparison is the `player`
        object the route derives from the JWT (get_current_player). This
        pins that structurally: calling with the ACTUAL owner succeeds,
        proving ownership is resolved from `player`, not from `gate_id` or
        any body field (there is no body field carrying an identity)."""
        owner_id = uuid.uuid4()
        db, owner, gate, tunnel = _setup(owner_id)
        result = warp_gate_service.set_gate_access_layers(
            db, owner, str(gate.id), toll_bypass=[str(uuid.uuid4())],
        )
        assert result["gate_id"] == str(gate.id)


# --- Attack goal #2: 404-vs-403 / existence-leak indistinguishability -------


@pytest.mark.unit
class TestNoExistenceLeak:
    def test_nonexistent_gate_and_someone_elses_gate_return_identical_404(self) -> None:
        """A gate that truly doesn't exist and a gate that exists but isn't
        the caller's must be INDISTINGUISHABLE to the caller — same status
        code, same detail string. If they differed, a non-owner could probe
        gate_ids to learn which UUIDs are live gates (existence leak)."""
        intruder = _fake_player()

        # Case A: gate_id doesn't exist at all (query returns None).
        db_missing = _FakeSession({WarpGate: _FakeQuery(first=None)})
        with pytest.raises(WarpGateError) as exc_missing:
            warp_gate_service.set_gate_access_layers(
                db_missing, intruder, str(uuid.uuid4()), toll_bypass=[],
            )

        # Case B: gate_id exists, is ACTIVE, but owned by someone else.
        owner_id = uuid.uuid4()
        db_owned, owner, gate, tunnel = _setup(owner_id)
        with pytest.raises(WarpGateError) as exc_owned:
            warp_gate_service.set_gate_access_layers(
                db_owned, intruder, str(gate.id), toll_bypass=[],
            )

        assert exc_missing.value.status_code == exc_owned.value.status_code == 404
        assert exc_missing.value.detail == exc_owned.value.detail == "Warp gate not found"

    def test_someone_elses_non_active_gate_does_not_leak_its_status(self) -> None:
        """A non-owner's gate that exists but is e.g. HARMONIZING must NOT
        surface the 400 'Gate is harmonizing — only an active gate can be
        administered' status-leaking message — that branch is reachable
        only AFTER the ownership check passes. A non-owner must always see
        the same 404 regardless of the target gate's real status."""
        owner_id = uuid.uuid4()
        intruder = _fake_player()
        gate = _fake_gate(owner_id, status=WarpGateStatus.HARMONIZING)
        db = _FakeSession({WarpGate: _FakeQuery(first=gate)})

        with pytest.raises(WarpGateError) as exc_info:
            warp_gate_service.set_gate_access_layers(db, intruder, str(gate.id), toll_bypass=[])

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Warp gate not found"


# --- Attack goal #3: input abuse ---------------------------------------------


@pytest.mark.unit
class TestFactionRepLayerInputAbuse:
    """_validate_faction_rep_layer's try/except wraps FactionType(...) in
    (ValueError, KeyError, TypeError, AttributeError) specifically because
    FactionType._missing_ (faction.py:36-41) does an unguarded
    `value.upper()` — a None or non-str faction_type raises AttributeError
    there, not the ValueError enum.__new__ normally raises. These tests
    call the validator directly (bypassing pydantic's `faction_type: str`
    schema coercion) to prove the defensive catch is complete even for a
    caller that isn't going through the FastAPI request-validation layer."""

    def test_none_faction_type_does_not_crash(self) -> None:
        # FactionType(None) raises AttributeError inside _missing_ (not the
        # ValueError/TypeError enum.__new__ normally raises) -- must surface
        # as a clean WarpGateError(400, ...), never an uncaught 500.
        with pytest.raises(WarpGateError) as exc_info:
            warp_gate_service._validate_faction_rep_layer(
                {"faction_type": None, "value": 100}, "faction_rep_min",
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.parametrize("bad_type", [123, 1.5, [], {}, True])
    def test_non_str_faction_type_does_not_crash(self, bad_type: Any) -> None:
        with pytest.raises(WarpGateError) as exc_info:
            warp_gate_service._validate_faction_rep_layer(
                {"faction_type": bad_type, "value": 100}, "faction_rep_min",
            )
        assert exc_info.value.status_code == 400

    def test_unrecognized_faction_type_string_rejected_not_silently_accepted(self) -> None:
        """A typo'd faction_type must be rejected here, not sail through to
        become a silent 'reject everyone' (rep_min) / 'reject no one'
        (rep_max) at traversal time — _faction_rep_value's permissive
        fallback resolves any unrecognized type to 0 rep for every player."""
        with pytest.raises(WarpGateError):
            warp_gate_service._validate_faction_rep_layer(
                {"faction_type": "NotARealFaction", "value": 0}, "faction_rep_min",
            )

    def test_bool_value_rejected_despite_bool_being_an_int_subclass(self) -> None:
        with pytest.raises(WarpGateError, match="whole number"):
            warp_gate_service._validate_faction_rep_layer(
                {"faction_type": "Federation", "value": True}, "faction_rep_min",
            )

    def test_negative_and_huge_value_accepted_but_only_self_affects_own_gate(self) -> None:
        """No range bound is enforced on `value` — an extreme threshold is
        possible, but this is the OWNER configuring their OWN gate (already
        reachable today via mode=PRIVATE). Pinning that this stays a clean
        no-crash path, not asserting it should be bounded (that's a game-
        design call, not a security one)."""
        result = warp_gate_service._validate_faction_rep_layer(
            {"faction_type": "Federation", "value": -999_999_999}, "faction_rep_min",
        )
        assert result == {"faction_type": "Federation", "value": -999_999_999}


@pytest.mark.unit
class TestTollBypassInputAbuse:
    def test_oversized_list_rejected(self) -> None:
        """Reuses _validate_uuid_list's MAX_ACCESS_LIST_ENTRIES (200) cap —
        same DoS guard whitelist/allies already have."""
        too_many = [str(uuid.uuid4()) for _ in range(201)]
        with pytest.raises(WarpGateError, match="at most"):
            warp_gate_service._validate_toll_bypass(too_many)

    def test_malformed_uuid_entries_rejected(self) -> None:
        with pytest.raises(WarpGateError, match="invalid UUID"):
            warp_gate_service._validate_toll_bypass(["not-a-uuid", "'; DROP TABLE players;--"])

    def test_full_call_with_malformed_body_leaves_jsonb_untouched(self) -> None:
        """End-to-end: a rejected set_gate_access_layers call (bad
        faction_type) must leave the tunnel's JSONB completely unchanged —
        same 'validate everything before locking/mutating anything'
        discipline set_gate_permissions already has for its toll bound."""
        owner_id = uuid.uuid4()
        db, owner, gate, tunnel = _setup(owner_id, access_requirements={"toll_amount": 500})

        with pytest.raises(WarpGateError):
            warp_gate_service.set_gate_access_layers(
                db, owner, str(gate.id),
                faction_rep_min={"faction_type": "NotReal", "value": 1},
                toll_bypass=[str(uuid.uuid4())],
            )

        assert tunnel.access_requirements == {"toll_amount": 500}


# --- Attack goal #4: owner cannot be locked out by their own layers --------


@pytest.mark.unit
class TestOwnerNeverSelfLocksViaLayers:
    def test_owner_bypasses_their_own_faction_rep_layers_at_traversal(self) -> None:
        """check_traversal_access returns before _check_faction_rep_layers
        ever runs when the traverser IS the owner — confirming an owner can
        never be locked out of their own gate by a faction_rep_min/max they
        just set via set_gate_access_layers (pre-existing behavior in
        check_traversal_access, exercised here against a tunnel actually
        carrying layers written by the new setter)."""
        owner = _fake_player()
        tunnel = _fake_tunnel(
            created_by_player_id=owner.id,
            access_requirements={
                "mode": "PUBLIC",
                "faction_rep_min": {"faction_type": "Federation", "value": 999_999},
            },
        )
        db = _FakeSession({})  # zero queries expected — owner short-circuits immediately
        # Must not raise for the owner despite an impossible rep_min threshold.
        warp_gate_service.check_traversal_access(db, owner, tunnel)
