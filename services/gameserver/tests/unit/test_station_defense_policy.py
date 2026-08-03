"""Unit tests for WO-STATION-DEFENSE-POLICY-LEVERS (pure helpers, no DB).

Covers normalize_defense_policy, evaluate_defense_dock_access (with mocked
reputation gate for faction mode), public_defense_policy_fields, and the
docking_fee_for punitive multiplier path.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.services import docking_service, port_ownership_service as po
from src.services.port_ownership_service import PortOwnershipError


def _player(pid=None):
    return SimpleNamespace(id=pid or uuid4())


def _station(owner_id=None, ownership=None, reputation_threshold=0, security_level="standard"):
    return SimpleNamespace(
        id=uuid4(),
        owner_id=owner_id,
        ownership=ownership,
        reputation_threshold=reputation_threshold,
        security_level=security_level,
        price_modifiers=None,
    )


class TestNormalizeDefensePolicy:
    def test_defaults_when_missing(self):
        policy = po.normalize_defense_policy(None)
        assert policy == {
            "docking_access": "open",
            "hostility_list": [],
            "punitive_fee_mult": 1.0,
            "defender_posture": "passive",
            "drone_allocation_pct": 100,
            "patrol_radius": 0,
        }

    def test_clamps_punitive_and_drone(self):
        policy = po.normalize_defense_policy({
            "docking_access": "whitelist",
            "hostility_list": ["a", 123],
            "punitive_fee_mult": 9.0,
            "drone_allocation_pct": 200,
            "patrol_radius": 5,
            "defender_posture": "aggressive",
        })
        assert policy["docking_access"] == "whitelist"
        assert policy["hostility_list"] == ["a", "123"]
        assert policy["punitive_fee_mult"] == 5.0
        assert policy["drone_allocation_pct"] == 100
        assert policy["patrol_radius"] == 0
        assert policy["defender_posture"] == "aggressive"

    def test_invalid_access_falls_back_to_open(self):
        policy = po.normalize_defense_policy({"docking_access": "nope"})
        assert policy["docking_access"] == "open"


class TestPublicDefensePolicyFields:
    def test_never_exposes_hostility_list(self):
        visitor = str(uuid4())
        station = _station(ownership={
            "defense_policy": {
                "docking_access": "hostile_deny",
                "hostility_list": [visitor],
                "punitive_fee_mult": 2.5,
            }
        })
        public = po.public_defense_policy_fields(station)
        assert public == {
            "docking_access": "hostile_deny",
            "punitive_fees_apply": True,
        }
        assert "hostility_list" not in public

    def test_punitive_false_at_default_mult(self):
        station = _station(ownership={"defense_policy": {"punitive_fee_mult": 1.0}})
        assert po.public_defense_policy_fields(station)["punitive_fees_apply"] is False


class TestEvaluateDefenseDockAccess:
    def test_open_allows_everyone(self):
        player = _player()
        station = _station(ownership={"defense_policy": {"docking_access": "open"}})
        result = po.evaluate_defense_dock_access(MagicMock(), station, player)
        assert result["allowed"] is True
        assert result["on_hostility_list"] is False
        assert result["punitive_fee_mult"] == 1.0

    def test_open_punitive_when_on_list(self):
        player = _player()
        station = _station(ownership={
            "defense_policy": {
                "docking_access": "open",
                "hostility_list": [str(player.id)],
                "punitive_fee_mult": 2.0,
            }
        })
        result = po.evaluate_defense_dock_access(MagicMock(), station, player)
        assert result["allowed"] is True
        assert result["on_hostility_list"] is True
        assert result["punitive_fee_mult"] == 2.0

    def test_whitelist_denies_non_listed(self):
        player = _player()
        station = _station(ownership={
            "defense_policy": {
                "docking_access": "whitelist",
                "hostility_list": [str(uuid4())],
            }
        })
        result = po.evaluate_defense_dock_access(MagicMock(), station, player)
        assert result["allowed"] is False
        assert "reason" in result

    def test_whitelist_allows_listed_with_punitive(self):
        player = _player()
        station = _station(ownership={
            "defense_policy": {
                "docking_access": "whitelist",
                "hostility_list": [str(player.id)],
                "punitive_fee_mult": 3.0,
            }
        })
        result = po.evaluate_defense_dock_access(MagicMock(), station, player)
        assert result["allowed"] is True
        assert result["punitive_fee_mult"] == 3.0

    def test_hostile_deny_blocks_listed(self):
        player = _player()
        station = _station(ownership={
            "defense_policy": {
                "docking_access": "hostile_deny",
                "hostility_list": [str(player.id)],
            }
        })
        result = po.evaluate_defense_dock_access(MagicMock(), station, player)
        assert result["allowed"] is False
        assert result["on_hostility_list"] is True
        assert result["punitive_fee_mult"] == 1.0  # denied → no punitive

    def test_hostile_deny_allows_others(self):
        player = _player()
        station = _station(ownership={
            "defense_policy": {
                "docking_access": "hostile_deny",
                "hostility_list": [str(uuid4())],
                "punitive_fee_mult": 4.0,
            }
        })
        result = po.evaluate_defense_dock_access(MagicMock(), station, player)
        assert result["allowed"] is True
        assert result["punitive_fee_mult"] == 1.0

    def test_owner_bypasses_whitelist_deny(self):
        owner = _player()
        station = _station(
            owner_id=owner.id,
            ownership={
                "defense_policy": {
                    "docking_access": "whitelist",
                    "hostility_list": [],
                    "punitive_fee_mult": 2.0,
                }
            },
        )
        result = po.evaluate_defense_dock_access(MagicMock(), station, owner)
        assert result["allowed"] is True

    def test_owner_on_list_pays_punitive(self):
        owner = _player()
        station = _station(
            owner_id=owner.id,
            ownership={
                "defense_policy": {
                    "docking_access": "hostile_deny",
                    "hostility_list": [str(owner.id)],
                    "punitive_fee_mult": 2.5,
                }
            },
        )
        result = po.evaluate_defense_dock_access(MagicMock(), station, owner)
        assert result["allowed"] is True
        assert result["punitive_fee_mult"] == 2.5

    def test_faction_uses_reputation_gate(self):
        player = _player()
        station = _station(
            ownership={"defense_policy": {"docking_access": "faction"}},
            reputation_threshold=50,
        )
        with patch.object(
            docking_service, "check_reputation_gate", return_value=(False, 10, 50)
        ):
            result = po.evaluate_defense_dock_access(MagicMock(), station, player)
        assert result["allowed"] is False
        assert "standing" in result["reason"]

    def test_faction_allows_when_gate_passes(self):
        player = _player()
        station = _station(
            ownership={"defense_policy": {"docking_access": "faction"}},
            reputation_threshold=0,
        )
        with patch.object(
            docking_service, "check_reputation_gate", return_value=(True, 0, 0)
        ):
            result = po.evaluate_defense_dock_access(MagicMock(), station, player)
        assert result["allowed"] is True


class TestSetDefensePolicyRejectsPatrol:
    def test_patrol_radius_gt_zero_rejected(self):
        owner = _player()
        station = _station(owner_id=owner.id, ownership={})
        db = MagicMock()
        # Bypass lock: return the same station.
        with patch.object(po, "_lock_station", return_value=station):
            with pytest.raises(PortOwnershipError) as exc:
                po.set_defense_policy(
                    db, station, owner, {"patrol_radius": 1, "docking_access": "open"}
                )
        assert "patrol is deferred" in exc.value.detail
        assert exc.value.status_code == 400


class TestDockingFeePunitive:
    def test_fee_multiplied_when_player_on_list(self):
        player = _player()
        station = _station(
            ownership={
                "defense_policy": {
                    "hostility_list": [str(player.id)],
                    "punitive_fee_mult": 2.0,
                }
            },
            security_level="standard",
        )
        # Force owner override so size/tier matrix is irrelevant.
        station.price_modifiers = {
            "docking_fee": 100,
            "docking_fee_enabled": True,
        }
        base = docking_service.docking_fee_for(station)
        assert base == 100
        punitive = docking_service.docking_fee_for(station, player=player)
        assert punitive == 200

    def test_fee_unchanged_when_player_not_on_list(self):
        player = _player()
        station = _station(
            ownership={
                "defense_policy": {
                    "hostility_list": [str(uuid4())],
                    "punitive_fee_mult": 3.0,
                }
            },
        )
        station.price_modifiers = {
            "docking_fee": 100,
            "docking_fee_enabled": True,
        }
        assert docking_service.docking_fee_for(station, player=player) == 100
