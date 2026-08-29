"""LEG-78 — NPC scheduler tuning resolution (npc_scheduler_config)."""
from __future__ import annotations

import pytest

from src.services.npc_scheduler_config import (
    DEFAULT_KIA_COOLDOWN_SECONDS,
    DEFAULT_OFF_DUTY_ROTATION_TARGET_PCT,
    GRACE_MAX_SECONDS,
    GRACE_MIN_SECONDS,
    NpcSchedulerConfigError,
    cycle_minutes,
    hop_cap_or_default,
    resolve_npc_scheduler_tuning,
    sample_grace_seconds,
    validate_config_mapping,
)


@pytest.mark.unit
class TestNpcSchedulerConfigDefaults:
    def test_defaults_preserve_shipped_behavior(self) -> None:
        t = resolve_npc_scheduler_tuning(role="federation_marshal", environ={})
        assert t.cycle_hours_default == 4.0
        assert cycle_minutes(t) == 240
        assert t.off_duty_rotation_target_pct == DEFAULT_OFF_DUTY_ROTATION_TARGET_PCT
        assert t.kia_cooldown_seconds == DEFAULT_KIA_COOLDOWN_SECONDS == 0
        assert t.engagement_no_response_grace_seconds is None
        assert t.route_engagement_max_distance_hops is None
        assert t.sources["cycle_hours_default"] == "default"

    def test_sentinel_role_default_cycle_hours(self) -> None:
        t = resolve_npc_scheduler_tuning(role="nexus_sentinel", environ={})
        assert t.cycle_hours_default == 3.0
        assert cycle_minutes(t) == 180

    def test_grace_samples_shipped_band(self) -> None:
        t = resolve_npc_scheduler_tuning(environ={})
        class _Rng:
            def randint(self, a: int, b: int) -> int:
                assert a == GRACE_MIN_SECONDS and b == GRACE_MAX_SECONDS
                return 600

        assert sample_grace_seconds(t, rng=_Rng()) == 600


@pytest.mark.unit
class TestNpcSchedulerConfigEnvOverride:
    def test_env_overrides_defaults(self) -> None:
        env = {
            "NPC_SCHEDULER_CYCLE_HOURS_DEFAULT": "6",
            "NPC_SCHEDULER_OFF_DUTY_ROTATION_TARGET_PCT": "0.35",
            "NPC_SCHEDULER_KIA_COOLDOWN_SECONDS": "604800",
            "NPC_SCHEDULER_ENGAGEMENT_NO_RESPONSE_GRACE_SECONDS": "420",
            "NPC_SCHEDULER_ROUTE_ENGAGEMENT_MAX_DISTANCE_HOPS": "7",
        }
        t = resolve_npc_scheduler_tuning(role="federation_marshal", environ=env)
        assert t.cycle_hours_default == 6.0
        assert t.off_duty_rotation_target_pct == 0.35
        assert t.kia_cooldown_seconds == 604800
        assert t.engagement_no_response_grace_seconds == 420
        assert t.route_engagement_max_distance_hops == 7
        assert all(s == "env" for s in t.sources.values())
        assert sample_grace_seconds(t) == 420
        assert hop_cap_or_default(t, class_default=5) == 7


@pytest.mark.unit
class TestNpcSchedulerConfigRosterOverride:
    def test_roster_beats_env(self) -> None:
        env = {
            "NPC_SCHEDULER_CYCLE_HOURS_DEFAULT": "6",
            "NPC_SCHEDULER_KIA_COOLDOWN_SECONDS": "100",
        }
        roster = {
            "cycle_hours_default": 2.5,
            "kia_cooldown_seconds": 50,
        }
        t = resolve_npc_scheduler_tuning(
            roster_config=roster,
            role="federation_marshal",
            environ=env,
        )
        assert t.cycle_hours_default == 2.5
        assert t.sources["cycle_hours_default"] == "roster"
        assert t.kia_cooldown_seconds == 50
        assert t.sources["kia_cooldown_seconds"] == "roster"
        # Unset on roster → still env
        assert t.off_duty_rotation_target_pct == DEFAULT_OFF_DUTY_ROTATION_TARGET_PCT
        assert t.sources["off_duty_rotation_target_pct"] == "default"


@pytest.mark.unit
class TestNpcSchedulerConfigIsolation:
    def test_per_role_cycle_defaults_differ(self) -> None:
        marshal = resolve_npc_scheduler_tuning(role="federation_marshal", environ={})
        sentinel = resolve_npc_scheduler_tuning(role="nexus_sentinel", environ={})
        assert marshal.cycle_hours_default != sentinel.cycle_hours_default

    def test_roster_config_isolated_per_call(self) -> None:
        a = resolve_npc_scheduler_tuning(
            roster_config={"route_engagement_max_distance_hops": 2},
            role="federation_marshal",
            environ={},
        )
        b = resolve_npc_scheduler_tuning(
            roster_config={"route_engagement_max_distance_hops": 9},
            role="federation_marshal",
            environ={},
        )
        assert a.route_engagement_max_distance_hops == 2
        assert b.route_engagement_max_distance_hops == 9


@pytest.mark.unit
class TestNpcSchedulerConfigInvalid:
    def test_invalid_cycle_rejected(self) -> None:
        with pytest.raises(NpcSchedulerConfigError):
            validate_config_mapping({"cycle_hours_default": 0}, origin="test")
        with pytest.raises(NpcSchedulerConfigError):
            validate_config_mapping({"cycle_hours_default": "abc"}, origin="test")
        with pytest.raises(NpcSchedulerConfigError):
            validate_config_mapping({"cycle_hours_default": True}, origin="test")

    def test_invalid_pct_no_silent_percent_coercion(self) -> None:
        with pytest.raises(NpcSchedulerConfigError):
            validate_config_mapping(
                {"off_duty_rotation_target_pct": 20}, origin="test"
            )

    def test_invalid_int_no_silent_truncation(self) -> None:
        with pytest.raises(NpcSchedulerConfigError):
            validate_config_mapping(
                {"kia_cooldown_seconds": 1.5}, origin="test"
            )
        with pytest.raises(NpcSchedulerConfigError):
            validate_config_mapping(
                {"route_engagement_max_distance_hops": 0}, origin="test"
            )

    def test_invalid_roster_falls_back_observably(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        with caplog.at_level(logging.ERROR):
            t = resolve_npc_scheduler_tuning(
                roster_config={"cycle_hours_default": "nope"},
                role="federation_marshal",
                environ={},
            )
        assert t.cycle_hours_default == 4.0
        assert t.sources["cycle_hours_default"] == "default"
        assert any("NPCRoster.config invalid" in r.message for r in caplog.records)

    def test_unknown_keys_ignored(self) -> None:
        out = validate_config_mapping(
            {"cycle_hours_default": 5, "invented_knob": 1},
            origin="test",
        )
        assert out == {"cycle_hours_default": 5.0}
