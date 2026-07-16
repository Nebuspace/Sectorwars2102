"""Pure-function smoke for intrasystem_movement_service (WO-ISP)."""
from datetime import datetime, timedelta, timezone

from src.services.intrasystem_movement_service import (
    MOVE_MS,
    ORIENT_MS,
    derive_pose,
    destination_weights,
    empty_idle_pose,
    pick_npc_destination,
    start_burn,
)


def test_destination_weights_role_bias():
    trader = destination_weights(archetype="TRADER", mission="commerce")
    science = destination_weights(mission="science")
    default = destination_weights()
    assert trader["habitable"] > trader["barren"]
    assert science["barren"] > trader["barren"]
    assert abs(sum(default.values()) - 1.0) < 1e-9
    # Ballpark Max asked for (~60/20/20), not exact.
    assert 0.50 <= default["habitable"] <= 0.70
    assert 0.10 <= default["outbound"] <= 0.30
    assert 0.10 <= default["barren"] <= 0.30


def test_pick_npc_destination_prefers_habitable_for_traders():
    pools = {
        "habitable": [
            {"x_pct": 30.0, "y_pct": 40.0, "bucket": "habitable", "target_kind": "station"},
            {"x_pct": 60.0, "y_pct": 50.0, "bucket": "habitable", "target_kind": "planet"},
        ],
        "barren": [
            {"x_pct": 80.0, "y_pct": 20.0, "bucket": "barren", "target_kind": "planet"},
        ],
        "outbound": [
            {"x_pct": 5.0, "y_pct": 50.0, "bucket": "outbound", "target_kind": "outbound"},
        ],
    }
    buckets = []
    for i in range(40):
        d = pick_npc_destination(
            pools,
            ship_key=f"trader-{i}",
            leg_index=i,
            from_xy=(40.0, 40.0),
            archetype="TRADER",
            mission="commerce",
        )
        buckets.append(d["bucket"])
    hab = buckets.count("habitable")
    barren = buckets.count("barren")
    assert hab > barren
    assert hab >= int(0.45 * len(buckets))


def test_burn_orients_then_translates_straight():
    now = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)
    idle = empty_idle_pose(1, "ship-a")
    idle["x_pct"] = 20.0
    idle["y_pct"] = 40.0
    idle["heading_deg"] = 0.0
    pose = start_burn(
        idle, to_x=80.0, to_y=40.0, sector_id=1, ship_key="ship-a", now=now
    )
    assert pose["phase"] == "orienting"
    assert pose["leg"]["from_x"] == 20.0

    mid_orient = derive_pose(pose, now + timedelta(milliseconds=ORIENT_MS // 2))
    assert mid_orient["phase"] == "orienting"
    assert mid_orient["x_pct"] == 20.0

    mid_move = derive_pose(pose, now + timedelta(milliseconds=ORIENT_MS + MOVE_MS // 2))
    assert mid_move["phase"] in ("accelerating", "gliding", "brake_turn", "braking")
    # Straight chord: y stays ~40
    assert abs(mid_move["y_pct"] - 40.0) < 0.2
    assert 20.0 < mid_move["x_pct"] < 80.0

    done = derive_pose(pose, now + timedelta(milliseconds=ORIENT_MS + MOVE_MS + 2000))
    assert done["phase"] == "idle"
    assert done["leg"] is None
    assert abs(done["x_pct"] - 80.0) < 0.01
