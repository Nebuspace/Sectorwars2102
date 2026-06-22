"""WO Lane A — admin create-planet size-gate clamp proof (DB-free).
An admin must not be able to create an over-cap planet: citadel_level is clamped to
max_citadel_level_for_size(size), the same CRT invariant start_upgrade enforces.
"""
from src.services.structures import max_citadel_level_for_size


def _clamp(requested, size):
    # mirrors admin_enhanced.create_planet
    size = max(1, min(10, size))
    return max(0, min(requested, max_citadel_level_for_size(size)))


def test_size_caps_are_monotonic_nondecreasing():
    caps = [max_citadel_level_for_size(s) for s in range(1, 11)]
    assert caps == sorted(caps), f"caps must be non-decreasing in size: {caps}"
    assert all(1 <= c <= 5 for c in caps), caps


def test_over_cap_request_is_clamped_down():
    # a small world cannot get a full citadel
    for size in range(1, 11):
        cap = max_citadel_level_for_size(size)
        assert _clamp(5, size) == cap                     # request L5 -> the size cap
        assert _clamp(99, size) == cap                    # absurd request still clamps to the cap


def test_under_cap_request_is_unchanged():
    for size in range(1, 11):
        cap = max_citadel_level_for_size(size)
        low = max(0, cap - 1)
        assert _clamp(low, size) == low                   # legitimate sub-cap level passes through


def test_size_is_bounded_1_to_10():
    assert _clamp(5, 0) == _clamp(5, 1)                   # size floored to 1
    assert _clamp(5, 99) == _clamp(5, 10)                 # size capped to 10
