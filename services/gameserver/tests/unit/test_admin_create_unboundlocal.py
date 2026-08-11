"""WO-FIX-ADMIN-CREATE-PLANET/PORT-UNBOUNDLOCAL.

A later `from src.models.X import X` inside the handler makes X a local for
the whole function. An earlier `db.query(X)` then raises UnboundLocalError
on every call. Pin that Planet/Station stay module globals.
"""

from src.api.routes import admin_comprehensive as ac


def test_create_planet_in_sector_does_not_bind_planet_locally():
    assert "Planet" not in ac.create_planet_in_sector.__code__.co_varnames


def test_create_port_in_sector_does_not_bind_station_locally():
    assert "Station" not in ac.create_port_in_sector.__code__.co_varnames
