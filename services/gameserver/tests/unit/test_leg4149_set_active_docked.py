"""LEG-4149: set-active ship route must allow switching while player.is_landed.

Unit test: verifies the is_landed guard was removed from set_active_ship.
Since the route handler is async + FastAPI, this test inspects the route
source directly rather than running a full HTTP client (DB-free).
"""
import ast
import textwrap
import pathlib

import pytest

ROUTE_SRC = (
    pathlib.Path(__file__).parents[2]
    / "src/api/routes/ship_upgrades.py"
)


@pytest.mark.unit
def test_is_landed_rejection_removed() -> None:
    """The is_landed guard ('Lift off before switching ships') must not exist
    in set_active_ship after LEG-4149."""
    source = ROUTE_SRC.read_text()
    assert "Lift off before switching ships" not in source, (
        "LEG-4149 regression: is_landed rejection string found in ship_upgrades.py. "
        "The docked set-active fix was reverted."
    )


@pytest.mark.unit
def test_is_landed_comment_present() -> None:
    """The rationale comment for removing the guard should be present."""
    source = ROUTE_SRC.read_text()
    assert "is_landed" in source and "LEG-4149" in source, (
        "Expected LEG-4149 rationale comment in ship_upgrades.py set_active_ship."
    )
