"""LEG-2607 / LEG-431 — mining harvest + license-expiry notifications (mining.md:258)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.services.mining_service import (
    LICENSE_EXPIRY_WARN_HOURS,
    MiningService,
    _MINING_LICENSE_EXPIRY_WARNED_KEY,
    build_harvest_notification_events,
)


def test_license_expiry_warn_hours_is_one_canonical_hour():
    assert LICENSE_EXPIRY_WARN_HOURS == 1


def test_build_harvest_notification_events_empty_on_failed_harvest():
    assert build_harvest_notification_events("u1", {"success": False}) == []


def test_build_harvest_notification_events_success_includes_yield_toast():
    user_id = uuid.uuid4()
    events = build_harvest_notification_events(
        user_id,
        {
            "success": True,
            "harvest_id": "h1",
            "ore": 10,
            "precious_metals": 0,
            "quantum_shards": 0,
        },
    )
    assert len(events) == 1
    frame = events[0]
    assert frame["type"] == "mining_harvest_notification"
    assert frame["subtype"] == "harvest_success"
    assert frame["user_id"] == str(user_id)
    assert frame["delivery"] == ["inbox", "toast"]
    assert frame["payload"]["ore"] == 10


def test_build_harvest_notification_events_separate_rare_and_trace_drops():
    events = build_harvest_notification_events(
        "user-abc",
        {
            "success": True,
            "harvest_id": "h2",
            "ore": 5,
            "precious_metals": 2,
            "quantum_shards": 1,
        },
    )
    assert len(events) == 3
    assert [e["subtype"] for e in events] == [
        "harvest_success",
        "precious_metals",
        "quantum_shards",
    ]
    assert events[1]["payload"]["drop_type"] == "precious_metals"
    assert events[1]["payload"]["amount"] == 2
    assert events[2]["payload"]["drop_type"] == "quantum_shards"
    assert events[2]["payload"]["amount"] == 1


class _LicenseQuery:
    def __init__(self, rows):
        self._rows = rows

    def join(self, *_a, **_k):
        return self

    def filter(self, *_a, **_k):
        return self

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows

    def query(self, *_models):
        return _LicenseQuery(self._rows)


def test_collect_license_expiry_warning_events_inside_one_hour_window():
    now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    license_id = uuid.uuid4()
    player_id = uuid.uuid4()
    user_id = uuid.uuid4()
    license_row = SimpleNamespace(
        id=license_id,
        region_id=uuid.uuid4(),
        sector_number=42,
        expires_at=now + timedelta(minutes=45),
    )
    player = SimpleNamespace(
        id=player_id,
        user_id=user_id,
        settings={},
    )
    svc = MiningService(db=_FakeDb([(license_row, player)]))
    events = svc.collect_license_expiry_warning_events(now=now)
    assert len(events) == 1
    assert events[0]["type"] == "mining_license_expiry_warning"
    assert events[0]["user_id"] == str(user_id)
    assert events[0]["delivery"] == ["inbox", "toast"]
    assert events[0]["payload"]["license_id"] == str(license_id)
    warned = player.settings[_MINING_LICENSE_EXPIRY_WARNED_KEY]
    assert warned[str(license_id)]


def test_collect_license_expiry_warning_events_dedupes_via_settings():
    now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    license_id = uuid.uuid4()
    user_id = uuid.uuid4()
    license_row = SimpleNamespace(
        id=license_id,
        region_id=None,
        sector_number=7,
        expires_at=now + timedelta(minutes=30),
    )
    player = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        settings={
            _MINING_LICENSE_EXPIRY_WARNED_KEY: {str(license_id): now.isoformat()}
        },
    )
    svc = MiningService(db=_FakeDb([(license_row, player)]))
    assert svc.collect_license_expiry_warning_events(now=now) == []
