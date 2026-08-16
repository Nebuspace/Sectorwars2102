"""LEG-88 — canonical natural-tunnel length → turn-cost bands."""
import math

import pytest

from src.services.warp_tunnel_length import (
    euclidean_hop_units,
    natural_tunnel_cost_fields,
    turn_cost_from_length,
)


class _Sec:
    def __init__(self, x, y, z):
        self.x_coord = x
        self.y_coord = y
        self.z_coord = z


class TestTurnCostBands:
    def test_boundary_5_is_short(self):
        assert turn_cost_from_length(5) == 1
        assert turn_cost_from_length(0) == 1
        assert turn_cost_from_length(4.999) == 1

    def test_boundary_6_and_10_are_medium(self):
        assert turn_cost_from_length(6) == 2
        assert turn_cost_from_length(10) == 2
        assert turn_cost_from_length(5.0001) == 2

    def test_boundary_11_plus_is_long_capped_at_3(self):
        assert turn_cost_from_length(11) == 3
        assert turn_cost_from_length(10.0001) == 3
        assert turn_cost_from_length(10_000) == 3

    def test_rejects_invalid_length(self):
        with pytest.raises(ValueError):
            turn_cost_from_length(-1)
        with pytest.raises(ValueError):
            turn_cost_from_length(float("nan"))
        with pytest.raises(ValueError):
            turn_cost_from_length(float("inf"))


class TestEuclideanHopUnits:
    def test_axis_aligned_distance(self):
        assert euclidean_hop_units(_Sec(0, 0, 0), _Sec(3, 4, 0)) == 5.0

    def test_dict_endpoints(self):
        a = {"x_coord": 0, "y_coord": 0, "z_coord": 0}
        b = {"x_coord": 0, "y_coord": 0, "z_coord": 11}
        assert euclidean_hop_units(a, b) == 11.0

    def test_missing_coords_raise(self):
        with pytest.raises(ValueError, match="missing"):
            euclidean_hop_units(_Sec(1, 2, 3), object())
        with pytest.raises(ValueError, match="missing"):
            euclidean_hop_units({"x_coord": 1, "y_coord": None, "z_coord": 0}, _Sec(0, 0, 0))

    def test_non_numeric_coords_raise(self):
        with pytest.raises(ValueError, match="not numeric"):
            euclidean_hop_units(
                {"x_coord": "nope", "y_coord": 0, "z_coord": 0},
                _Sec(0, 0, 0),
            )


class TestNaturalTunnelCostFields:
    def test_mirrors_length_and_traversal_cost(self):
        length, turn_cost, props = natural_tunnel_cost_fields(
            _Sec(0, 0, 0), _Sec(0, 0, 7), base_properties={"stability_rating": 80}
        )
        assert length == 7.0
        assert turn_cost == 2
        assert props["length"] == 7.0
        assert props["traversal_cost"] == 2
        assert props["stability_rating"] == 80

    def test_interregional_long_haul_band(self):
        # Typical Nexus attachment span should land in a real band without
        # inventing coordinates — fixture uses known 3D endpoints only.
        length, turn_cost, props = natural_tunnel_cost_fields(
            _Sec(0, 0, 0), _Sec(20, 0, 0)
        )
        assert length == 20.0
        assert turn_cost == 3
        assert props["traversal_cost"] == 3

    def test_short_cluster_internal(self):
        length, turn_cost, _ = natural_tunnel_cost_fields(
            _Sec(1, 1, 1), _Sec(2, 2, 1)
        )
        assert math.isclose(length, math.sqrt(2))
        assert turn_cost == 1
