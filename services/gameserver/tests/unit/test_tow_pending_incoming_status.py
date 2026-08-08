"""WO-WIRE-TOW-CONSENT-UI — pending_incoming discovery for GET /tow/status."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from src.services.tow_service import REQUEST_PENDING, TowService


def test_find_pending_hauler_for_target_returns_pending_hauler():
    target_id = uuid4()
    hauler_id = uuid4()
    hauler = SimpleNamespace(
        id=hauler_id,
        is_destroyed=False,
        tow_state={
            "request_state": REQUEST_PENDING,
            "towed_ship_id": str(target_id),
            "requested_at": "2099-01-01T00:00:00+00:00",
        },
    )
    db = MagicMock()
    q = MagicMock()
    db.query.return_value = q
    q.filter.return_value = q
    q.all.return_value = [hauler]

    found = TowService(db).find_pending_hauler_for_target(target_id)
    assert found is hauler


def test_find_pending_hauler_for_target_skips_locked():
    target_id = uuid4()
    hauler = SimpleNamespace(
        id=uuid4(),
        is_destroyed=False,
        tow_state={
            "request_state": "LOCKED",
            "towed_ship_id": str(target_id),
        },
    )
    db = MagicMock()
    q = MagicMock()
    db.query.return_value = q
    q.filter.return_value = q
    q.all.return_value = [hauler]

    assert TowService(db).find_pending_hauler_for_target(target_id) is None
