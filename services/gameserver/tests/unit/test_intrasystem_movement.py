"""Pure-function smoke for intrasystem_movement_service (WO-ISP)."""
from datetime import datetime, timedelta, timezone

from src.services.intrasystem_movement_service import (
    ACCEL_MS,
    COAST_MS,
    FLIP_MS,
    HALT_BRAKE_MS,
    HALT_FLIP_MS,
    MOVE_MS,
    ORIENT_MS,
    PROFILE_MS,
    SETTLE_MS,
    _derive_burn,
    _derive_halt,
    derive_pose,
    destination_weights,
    empty_idle_pose,
    heading_deg,
    pick_npc_destination,
    start_burn,
    start_halt,
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


# ---------------------------------------------------------------------------
# heading_deg normalized to [0,360) at every write site (WO-ISP-DOCKPROX+
# QUEUE-ISP-WS-EMIT-THREAD item 3). Max's live fly-by observed 531deg
# server-side -- a completed burn's arrival heading is face=prograde+360,
# the SAME unwrapped "continuous spin" convention the client's own
# otherShipFlightPose uses for its final_orient sweep (harmless there --
# CSS rotate() is periodic) -- but that value gets PERSISTED as the ship's
# long-term rest heading and re-read as the NEXT leg's `parked` baseline,
# so it must be canonical [0,360) at the point it's stored.
# ---------------------------------------------------------------------------


def test_heading_deg_531_repro_burn_arrival_normalizes():
    """The exact reported symptom: a burn whose prograde is ~171deg would
    have arrived at face=531deg pre-fix. Post-fix it arrives at 171deg."""
    leg = {"kind": "burn", "profile": {}}
    settle_end = ORIENT_MS + MOVE_MS + SETTLE_MS
    result = _derive_burn(
        fx=10.0, fy=10.0, tx=90.0, ty=10.0,
        prograde=171.0, parked=0.0,
        elapsed_ms=settle_end + 5000,  # well past arrival
        leg=leg,
    )
    assert result["phase"] == "idle"
    assert result["heading_deg"] == 171.0  # NOT 531.0
    assert 0.0 <= result["heading_deg"] < 360.0


def test_heading_deg_halt_arrival_normalizes_past_360():
    """retrograde = prograde+180 overflows for any prograde >= 180 -- pin
    a case that would have stored e.g. 200+180=380deg pre-fix."""
    leg = {"kind": "halt", "profile": {}}
    total = HALT_FLIP_MS + HALT_BRAKE_MS
    result = _derive_halt(
        fx=10.0, fy=10.0, tx=14.0, ty=10.0,
        prograde=200.0, parked=0.0,
        elapsed_ms=total + 5000,
        leg=leg,
    )
    assert result["phase"] == "idle"
    assert result["heading_deg"] == 20.0  # 200+180=380 -> 20, NOT 380
    assert 0.0 <= result["heading_deg"] < 360.0


def test_heading_deg_function_never_returns_negative():
    """atan2's own range is (-180,180] -- e.g. up-left in %-space (ty<fy AND
    tx<fx, both atan2 args negative -> third quadrant) is naturally negative
    (raw atan2 here: -166.9deg); must normalize to [0,360) at the source
    too. Mack's fix (this test's prior input, heading_deg(50,50,10,90), had
    a raw atan2 of +166.9deg -- it would have passed even with
    _normalize_heading_deg deleted; this one genuinely exercises the
    negative branch)."""
    hdg = heading_deg(50.0, 50.0, 10.0, 10.0)
    assert 0.0 <= hdg < 360.0
    assert abs(hdg - 193.09628220019798) < 1e-6


def test_derive_pose_self_heals_a_stored_out_of_range_heading():
    """A pose persisted BEFORE this fix shipped (heading_deg=531, no active
    leg) must self-heal to canonical range the next time it's read, not
    just stop growing on future writes."""
    pose = {"x_pct": 40.0, "y_pct": 40.0, "heading_deg": 531.0, "phase": "idle", "burning": False, "leg": None}
    sample = derive_pose(pose)
    assert sample["heading_deg"] == 171.0
    assert 0.0 <= sample["heading_deg"] < 360.0


def test_start_burn_normalizes_a_stale_corrupted_base_heading():
    """A new leg's parked_heading_deg baseline must never inherit an
    already-corrupted stored value (the self-heal from derive_pose above
    must actually reach the NEXT leg's own persisted fields)."""
    now = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)
    stale = {"x_pct": 20.0, "y_pct": 40.0, "heading_deg": 531.0, "phase": "idle", "burning": False, "leg": None}
    pose = start_burn(stale, to_x=80.0, to_y=40.0, sector_id=1, ship_key="ship-b", now=now)
    assert pose["heading_deg"] == 171.0
    assert pose["leg"]["parked_heading_deg"] == 171.0


def test_start_halt_arrival_reachable_and_normalized_end_to_end():
    now = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)
    idle = empty_idle_pose(1, "ship-c")
    idle["x_pct"] = 20.0
    idle["y_pct"] = 40.0
    idle["heading_deg"] = 171.0
    burn_pose = start_burn(idle, to_x=90.0, to_y=40.0, sector_id=1, ship_key="ship-c", now=now)
    mid = now + timedelta(milliseconds=ORIENT_MS + MOVE_MS // 2)
    halted = start_halt(burn_pose, sector_id=1, ship_key="ship-c", now=mid)
    assert halted["leg"]["kind"] == "halt"

    arrived = derive_pose(halted, mid + timedelta(milliseconds=HALT_FLIP_MS + HALT_BRAKE_MS + 2000))
    assert arrived["phase"] == "idle"
    assert 0.0 <= arrived["heading_deg"] < 360.0


# ---------------------------------------------------------------------------
# Mack's FAIL (2026-07-16): a mid-course redirect promotes a TRANSIENT,
# deliberately-unwrapped derive_pose() sample (brake_turn: prograde+180*ft)
# into PERSISTED storage via start_burn/start_halt without normalizing it --
# a storage boundary the earlier heading_deg fix missed. Reproduced live:
# persisted heading 539.89deg.
# ---------------------------------------------------------------------------


def test_start_burn_redirect_mid_brake_turn_normalizes_persisted_heading():
    """First leg prograde~359.9deg; redirect sampled 1ms before flip_end
    (ft~0.999, deep in brake_turn) -- the transient sample itself is
    legitimately unwrapped (>360, matches the client's own convention,
    not a bug on its own), but start_burn must never PERSIST that raw
    value. Pre-fix this promoted heading_deg~539.89 into storage."""
    now = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)
    leg = {
        "kind": "burn", "from_x": 20.0, "from_y": 40.0, "to_x": 80.0, "to_y": 40.0,
        "started_at": now.isoformat(), "prograde_deg": 359.9, "parked_heading_deg": 0.0,
        "profile": PROFILE_MS,
    }
    pose = {
        "x_pct": 20.0, "y_pct": 40.0, "heading_deg": 0.0,
        "phase": "orienting", "burning": False, "leg": leg,
    }
    flip_end_ms = ORIENT_MS + ACCEL_MS + COAST_MS + FLIP_MS
    redirect_now = now + timedelta(milliseconds=flip_end_ms - 1)

    # Confirm the transient sample really is deep in brake_turn and really
    # is raw/unwrapped (>360) at this instant -- that part is correct,
    # unchanged behavior, not what this test is pinning.
    sample = derive_pose(pose, redirect_now)
    assert sample["phase"] == "brake_turn"
    assert sample["heading_deg"] > 360.0

    redirected = start_burn(
        pose, to_x=80.0, to_y=90.0, sector_id=1, ship_key="ship-redirect", now=redirect_now,
    )
    assert 0.0 <= redirected["heading_deg"] < 360.0
    assert 0.0 <= redirected["leg"]["parked_heading_deg"] < 360.0


def test_start_halt_redirect_mid_brake_turn_normalizes_persisted_heading():
    """start_halt twin of the above -- halting mid-flight while deep in a
    transient (unwrapped) phase must not persist that raw value either."""
    now = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)
    leg = {
        "kind": "burn", "from_x": 20.0, "from_y": 40.0, "to_x": 80.0, "to_y": 40.0,
        "started_at": now.isoformat(), "prograde_deg": 359.9, "parked_heading_deg": 0.0,
        "profile": PROFILE_MS,
    }
    pose = {
        "x_pct": 20.0, "y_pct": 40.0, "heading_deg": 0.0,
        "phase": "orienting", "burning": False, "leg": leg,
    }
    flip_end_ms = ORIENT_MS + ACCEL_MS + COAST_MS + FLIP_MS
    halt_now = now + timedelta(milliseconds=flip_end_ms - 1)

    sample = derive_pose(pose, halt_now)
    assert sample["phase"] == "brake_turn"
    assert sample["heading_deg"] > 360.0

    halted = start_halt(pose, sector_id=1, ship_key="ship-redirect-halt", now=halt_now)
    assert 0.0 <= halted["heading_deg"] < 360.0
    assert 0.0 <= halted["leg"]["parked_heading_deg"] < 360.0
    assert 0.0 <= halted["leg"]["prograde_deg"] < 360.0
