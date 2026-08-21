"""Unit tests for outlaw_base_raid_service (LEG-INI-19).

Covers discovery visibility, raid completion idempotency/rollback,
KIA of sleepers, influence + loot when operator-configured, and
cooldown boundaries. Uses a small FakeSession (pirate-holding test idiom).
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from src.models.npc_character import (
    NPCActivity,
    NPCArchetype,
    NPCCharacter,
    NPCDeathLog,
    NPCLifecycleStage,
    NPCStatus,
)
from src.models.outlaw_base import OutlawBase
from src.services import outlaw_base_raid_service as obs
from src.services.outlaw_base_raid_service import (
    CONFIG_HOSTILE_FACTION_ID,
    CONFIG_INFLUENCE_DELTA,
    CONFIG_LOOT_SHARE_FRACTION,
    DiscoveryContext,
    RAID_COOLDOWN,
    complete_outlaw_base_raid,
    evaluate_discovery_requirements,
    is_on_raid_cooldown,
    is_outlaw_base_visible,
    list_visible_outlaw_bases,
)


NOW = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)


def _base(**overrides) -> OutlawBase:
    row = OutlawBase(
        id=uuid.uuid4(),
        name="Rogue Nest",
        sector_id=42,
        home_region_id=uuid.uuid4(),
        faction_code="pirates",
        archetype=NPCArchetype.HOSTILE_RAIDER,
        capacity=4,
        current_occupants_count=0,
        assigned_npc_ids=[],
        is_player_discoverable=True,
        discovery_requirements=None,
        defenses={},
        amenities={},
        loot_inventory={},
        raid_audit_log=[],
        relocation_pending=False,
    )
    for k, v in overrides.items():
        setattr(row, k, v)
    return row


def _npc(*, sleeping=True, **overrides) -> NPCCharacter:
    npc = NPCCharacter(
        id=uuid.uuid4(),
        name="Sleepy Pete",
        faction_code="pirates",
        archetype=NPCArchetype.HOSTILE_RAIDER,
        status=NPCStatus.OFF_DUTY,
        current_activity=NPCActivity.SLEEP if sleeping else NPCActivity.PATROL,
        lifecycle_stage=NPCLifecycleStage.ACTIVE,
        daily_schedule={},
        current_sector_id=42,
    )
    for k, v in overrides.items():
        setattr(npc, k, v)
    return npc


class _FakeQuery:
    def __init__(self, session: "_FakeSession", model):
        self._session = session
        self._model = model
        self._filters: List[Any] = []
        self._for_update = False

    def filter(self, *args):
        self._filters.extend(args)
        return self

    def populate_existing(self):
        return self

    def with_for_update(self):
        self._for_update = True
        return self

    def first(self):
        rows = self._session.rows_for(self._model)
        # Extremely small matcher: equality on .id when present in filters'
        # compiled form is unavailable — tests pass direct object identity
        # via session.register and look up by scanning attribute equality
        # against simple BinaryExpression left/right when possible.
        for row in rows:
            if self._match(row):
                return row
        return None

    def _match(self, row) -> bool:
        if not self._filters:
            return True
        for f in self._filters:
            # SQLAlchemy BinaryExpression: left.key / right.value
            left = getattr(f, "left", None)
            right = getattr(f, "right", None)
            if left is None or right is None:
                continue
            key = getattr(left, "key", None)
            value = getattr(right, "value", right)
            if key is None:
                continue
            if getattr(row, key, None) != value:
                return False
        return True


class _FakeSession:
    def __init__(self):
        self._rows: Dict[type, List[Any]] = {}
        self.added: List[Any] = []
        self.flushed = 0
        self.nested = 0
        self.rollback_nested = 0
        self._abort_nested = False

    def register(self, obj):
        self._rows.setdefault(type(obj), []).append(obj)
        # Also index NPCCharacter / OutlawBase by class used in queries
        return obj

    def rows_for(self, model):
        # Match exact class or OutlawBase/NPCCharacter instances stored under subclass
        out = list(self._rows.get(model, []))
        for cls, rows in self._rows.items():
            if cls is not model and issubclass(cls, model):
                out.extend(rows)
        # Also return rows whose runtime class equals model
        for rows in self._rows.values():
            for r in rows:
                if type(r) is model and r not in out:
                    out.append(r)
        return out

    def query(self, model):
        return _FakeQuery(self, model)

    def add(self, obj):
        self.added.append(obj)
        self.register(obj)

    def flush(self):
        self.flushed += 1
        if self._abort_nested:
            raise RuntimeError("forced flush failure")

    @contextmanager
    def begin_nested(self):
        self.nested += 1
        try:
            yield self
        except Exception:
            self.rollback_nested += 1
            raise


def _ok_combat(db, base, attacker_id):
    return {"success": True, "message": "ok"}


def _fail_combat(db, base, attacker_id):
    return {"success": False, "message": "turrets still up"}


# ---------------------------------------------------------------------------
# Discovery eligibility
# ---------------------------------------------------------------------------


class TestDiscoveryEligibility:
    def test_null_requirements_always_eligible(self):
        r = evaluate_discovery_requirements(None, DiscoveryContext())
        assert r.eligible is True

    def test_hidden_when_not_discoverable(self):
        base = _base(is_player_discoverable=False)
        r = is_outlaw_base_visible(base, DiscoveryContext())
        assert r.eligible is False
        assert "hidden" in r.reasons[0]

    def test_unknown_keys_fail_closed(self):
        r = evaluate_discovery_requirements(
            {"min_faction_intel_rep": 1, "made_up_key": True},
            DiscoveryContext(faction_intel_rep=99),
        )
        assert r.eligible is False
        assert "made_up_key" in r.unknown_keys

    def test_ineligible_low_intel(self):
        r = evaluate_discovery_requirements(
            {"min_faction_intel_rep": 100},
            DiscoveryContext(faction_intel_rep=50),
        )
        assert r.eligible is False

    def test_eligible_when_all_keys_met(self):
        r = evaluate_discovery_requirements(
            {
                "min_faction_intel_rep": 100,
                "requires_item": "intel-chip",
                "requires_clue_count": 3,
            },
            DiscoveryContext(
                faction_intel_rep=100,
                item_ids={"intel-chip"},
                clue_count=3,
            ),
        )
        assert r.eligible is True

    def test_unresolved_intel_source_is_ineligible_and_flagged(self):
        r = evaluate_discovery_requirements(
            {"min_faction_intel_rep": 10},
            DiscoveryContext(),
        )
        assert r.eligible is False
        assert any("unresolved:min_faction_intel_rep" in x for x in r.reasons)
        assert r.decision_needed

    def test_list_visible_filters(self):
        hidden = _base(is_player_discoverable=False)
        open_base = _base(is_player_discoverable=True, discovery_requirements=None)
        gated = _base(
            is_player_discoverable=True,
            discovery_requirements={"requires_clue_count": 5},
        )
        ctx = DiscoveryContext(clue_count=5)
        visible = list_visible_outlaw_bases([hidden, open_base, gated], ctx)
        assert open_base in visible
        assert gated in visible
        assert hidden not in visible


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------


class TestCooldown:
    def test_no_cooldown_when_unset(self):
        assert is_on_raid_cooldown(_base(), now=NOW) is False

    def test_active_cooldown(self):
        base = _base(raid_cooldown_until=NOW + timedelta(days=1))
        assert is_on_raid_cooldown(base, now=NOW) is True

    def test_expired_cooldown_allows_raid(self):
        base = _base(raid_cooldown_until=NOW - timedelta(seconds=1))
        assert is_on_raid_cooldown(base, now=NOW) is False


# ---------------------------------------------------------------------------
# Raid completion
# ---------------------------------------------------------------------------


class TestCompleteRaid:
    def test_hidden_base_rejected(self):
        db = _FakeSession()
        base = db.register(_base(is_player_discoverable=False))
        result = complete_outlaw_base_raid(
            db,
            base_id=base.id,
            attacker_id=uuid.uuid4(),
            completion_id=uuid.uuid4(),
            discovery_context=DiscoveryContext(),
            combat_resolver=_ok_combat,
            now=NOW,
        )
        assert result.success is False
        assert base.last_raid_completion_id is None

    def test_ineligible_requirements_rejected(self):
        db = _FakeSession()
        base = db.register(
            _base(discovery_requirements={"min_faction_intel_rep": 200})
        )
        result = complete_outlaw_base_raid(
            db,
            base_id=base.id,
            attacker_id=uuid.uuid4(),
            completion_id=uuid.uuid4(),
            discovery_context=DiscoveryContext(faction_intel_rep=10),
            combat_resolver=_ok_combat,
            now=NOW,
        )
        assert result.success is False
        assert base.raid_cooldown_until is None

    def test_cooldown_blocks_re_raid(self):
        db = _FakeSession()
        base = db.register(
            _base(raid_cooldown_until=NOW + timedelta(days=10))
        )
        result = complete_outlaw_base_raid(
            db,
            base_id=base.id,
            attacker_id=uuid.uuid4(),
            completion_id=uuid.uuid4(),
            discovery_context=DiscoveryContext(),
            combat_resolver=_ok_combat,
            now=NOW,
        )
        assert result.success is False
        assert "cooldown" in result.message

    def test_combat_failure_rolls_back_outcomes(self):
        db = _FakeSession()
        sleeper = _npc(sleeping=True)
        base = db.register(
            _base(
                assigned_npc_ids=[str(sleeper.id)],
                current_occupants_count=1,
                loot_inventory={"ore": 100},
            )
        )
        db.register(sleeper)
        result = complete_outlaw_base_raid(
            db,
            base_id=base.id,
            attacker_id=uuid.uuid4(),
            completion_id=uuid.uuid4(),
            discovery_context=DiscoveryContext(),
            operator_config={CONFIG_LOOT_SHARE_FRACTION: 0.5},
            combat_resolver=_fail_combat,
            now=NOW,
        )
        assert result.success is False
        assert base.last_raid_completion_id is None
        assert base.loot_inventory == {"ore": 100}
        assert sleeper.status != NPCStatus.KIA
        assert base.combat_lock_held_by is None

    def test_success_kia_loot_cooldown_audit(self):
        db = _FakeSession()
        sleeper = _npc(sleeping=True)
        patrol = _npc(sleeping=False, name="Patrol Pat")
        base = db.register(
            _base(
                assigned_npc_ids=[str(sleeper.id), str(patrol.id)],
                current_occupants_count=2,
                loot_inventory={"credits": 100, "spice": 10},
            )
        )
        db.register(sleeper)
        db.register(patrol)
        attacker = uuid.uuid4()
        cid = uuid.uuid4()
        result = complete_outlaw_base_raid(
            db,
            base_id=base.id,
            attacker_id=attacker,
            completion_id=cid,
            discovery_context=DiscoveryContext(),
            operator_config={CONFIG_LOOT_SHARE_FRACTION: 0.5},
            combat_resolver=_ok_combat,
            now=NOW,
        )
        assert result.success is True
        assert result.kia_npc_ids == [str(sleeper.id)]
        assert sleeper.status == NPCStatus.KIA
        assert sleeper.lifecycle_stage == NPCLifecycleStage.KIA
        assert patrol.status != NPCStatus.KIA
        assert result.loot_taken == {"credits": 50, "spice": 5}
        assert base.loot_inventory == {"credits": 50, "spice": 5}
        assert base.raid_cooldown_until == NOW + RAID_COOLDOWN
        assert base.last_raid_completion_id == cid
        assert base.relocation_pending is True
        assert any("DECISION-NEEDED" in d for d in result.decision_needed)
        assert any(
            isinstance(x, NPCDeathLog) and x.npc_id == sleeper.id for x in db.added
        )
        assert base.raid_audit_log and base.raid_audit_log[-1]["completion_id"] == str(
            cid
        )

    def test_idempotent_replay_same_completion_id(self):
        db = _FakeSession()
        base = db.register(_base())
        attacker = uuid.uuid4()
        cid = uuid.uuid4()
        first = complete_outlaw_base_raid(
            db,
            base_id=base.id,
            attacker_id=attacker,
            completion_id=cid,
            discovery_context=DiscoveryContext(),
            operator_config={CONFIG_LOOT_SHARE_FRACTION: 0.0},
            combat_resolver=_ok_combat,
            now=NOW,
        )
        assert first.success and not first.idempotent_replay
        second = complete_outlaw_base_raid(
            db,
            base_id=base.id,
            attacker_id=attacker,
            completion_id=cid,
            discovery_context=DiscoveryContext(),
            combat_resolver=_ok_combat,
            now=NOW + timedelta(minutes=1),
        )
        assert second.success and second.idempotent_replay

    def test_concurrent_second_completion_blocked_by_cooldown(self):
        db = _FakeSession()
        base = db.register(_base())
        attacker = uuid.uuid4()
        complete_outlaw_base_raid(
            db,
            base_id=base.id,
            attacker_id=attacker,
            completion_id=uuid.uuid4(),
            discovery_context=DiscoveryContext(),
            operator_config={CONFIG_LOOT_SHARE_FRACTION: 0.0},
            combat_resolver=_ok_combat,
            now=NOW,
        )
        other = complete_outlaw_base_raid(
            db,
            base_id=base.id,
            attacker_id=uuid.uuid4(),
            completion_id=uuid.uuid4(),
            discovery_context=DiscoveryContext(),
            combat_resolver=_ok_combat,
            now=NOW + timedelta(hours=1),
        )
        assert other.success is False
        assert "cooldown" in other.message

    def test_cooldown_boundary_exact_expiry_allows(self):
        db = _FakeSession()
        base = db.register(
            _base(raid_cooldown_until=NOW)  # equal → not active (until > now)
        )
        result = complete_outlaw_base_raid(
            db,
            base_id=base.id,
            attacker_id=uuid.uuid4(),
            completion_id=uuid.uuid4(),
            discovery_context=DiscoveryContext(),
            operator_config={CONFIG_LOOT_SHARE_FRACTION: 0.0},
            combat_resolver=_ok_combat,
            now=NOW,
        )
        assert result.success is True

    def test_influence_when_configured(self, monkeypatch):
        db = _FakeSession()
        base = db.register(_base(sector_id=7))
        sector_uuid = uuid.uuid4()
        faction_id = uuid.uuid4()
        calls = []

        class _Sector:
            id = sector_uuid
            sector_id = 7

        def fake_query(model):
            q = _FakeQuery(db, model)
            if model.__name__ == "Sector":
                # register sector for lookup
                db._rows.setdefault(type(_Sector()), [])
                return q
            return q

        db.register(SimpleNamespace(id=sector_uuid, sector_id=7))

        # Patch Sector query path used inside _apply_influence_reduction
        import src.services.outlaw_base_raid_service as mod

        real_apply = mod._apply_influence_reduction

        def wrapped(db_, *, base, faction_id, delta):
            calls.append((faction_id, delta))
            return True

        monkeypatch.setattr(mod, "_apply_influence_reduction", wrapped)

        result = complete_outlaw_base_raid(
            db,
            base_id=base.id,
            attacker_id=uuid.uuid4(),
            completion_id=uuid.uuid4(),
            discovery_context=DiscoveryContext(),
            operator_config={
                CONFIG_LOOT_SHARE_FRACTION: 0.0,
                CONFIG_INFLUENCE_DELTA: -12.5,
                CONFIG_HOSTILE_FACTION_ID: str(faction_id),
            },
            combat_resolver=_ok_combat,
            now=NOW,
        )
        assert result.success is True
        assert result.influence_applied is True
        assert calls and calls[0][0] == faction_id
        assert calls[0][1] == -12.5

    def test_loot_unconfigured_flags_decision_needed(self):
        db = _FakeSession()
        base = db.register(_base(loot_inventory={"ore": 40}))
        result = complete_outlaw_base_raid(
            db,
            base_id=base.id,
            attacker_id=uuid.uuid4(),
            completion_id=uuid.uuid4(),
            discovery_context=DiscoveryContext(),
            operator_config={},  # no loot fraction
            combat_resolver=_ok_combat,
            now=NOW,
        )
        assert result.success is True
        assert result.loot_taken == {}
        assert base.loot_inventory == {"ore": 40}
        assert any("loot_share_fraction" in d for d in result.decision_needed)

    def test_flush_failure_rolls_back_nested(self):
        db = _FakeSession()
        base = db.register(_base())
        db._abort_nested = True
        with pytest.raises(RuntimeError):
            complete_outlaw_base_raid(
                db,
                base_id=base.id,
                attacker_id=uuid.uuid4(),
                completion_id=uuid.uuid4(),
                discovery_context=DiscoveryContext(),
                operator_config={CONFIG_LOOT_SHARE_FRACTION: 0.0},
                combat_resolver=_ok_combat,
                now=NOW,
            )
        # FakeSession mirrors SQLAlchemy savepoint abort counting; in-memory
        # attribute mutations are not expired (same as a real Session without
        # expire_on_commit) — the load-bearing signal is nested rollback.
        assert db.rollback_nested == 1
