"""LEG-2841 — profession prerequisite catalog kinds are placeable."""
from src.services.building_catalog import BUILDING_CATALOG, assert_catalog_valid, get
from src.services.structures import can_place, place


REQUIRED = ("ORBITAL_SHIPYARD", "MILITARY_ACADEMY", "TERRAFORMING_LAB")


def _empty_grid(cols=8, rows=8):
    plots = []
    for y in range(rows):
        for x in range(cols):
            plots.append(
                {
                    "x": x,
                    "y": y,
                    "cleared": True,
                    "hazard": None,
                    "building_id": None,
                    "surveyed": True,
                }
            )
    return {
        "v": 1,
        "grid": {"cols": cols, "rows": rows},
        "plots": plots,
        "buildings": [],
        "instability": 0,
    }


def test_profession_prerequisite_kinds_in_catalog():
    assert_catalog_valid()
    for kind in REQUIRED:
        row = get(kind)
        assert row is not None, kind
        assert row["kind"] == kind
        assert kind in BUILDING_CATALOG


def test_profession_prerequisite_kinds_placeable():
    for kind in REQUIRED:
        structures = _empty_grid()
        ok, reason = can_place(structures, kind, 0, 0)
        assert ok, f"{kind}: {reason}"
        building = place(structures, kind, 0, 0, level=2, complete_at=None)
        assert building["kind"] == kind
        assert building["level"] == 2
        assert any(b["kind"] == kind for b in structures["buildings"])
