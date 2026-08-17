"""LEG-431 — mining harvest / rare-drop / license-expiry inbox notifications."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.services.mining_service import MiningService


def test_notify_harvest_sends_success_and_rare_drop_messages():
    db = MagicMock()
    player_id = uuid.uuid4()
    harvest_id = uuid.uuid4()
    svc = MiningService(db)
    sent = []

    def fake_send(pid, *, subject, content, priority="normal"):
        sent.append(
            {
                "player_id": pid,
                "subject": subject,
                "content": content,
                "priority": priority,
            }
        )
        return True

    with patch.object(svc, "_send_system_inbox", side_effect=fake_send):
        svc._notify_harvest_completed(
            player_id,
            ore=7,
            precious_metals=2,
            quantum_shards=1,
            harvest_id=harvest_id,
        )

    subjects = [s["subject"] for s in sent]
    assert any(s.startswith("mining.harvest:") for s in subjects)
    assert any(s.startswith("mining.precious_metals:") for s in subjects)
    assert any(s.startswith("mining.quantum_shards:") for s in subjects)
    assert any("7 ore" in s["content"] for s in sent)


def test_notify_harvest_soft_fail_does_not_raise():
    db = MagicMock()
    svc = MiningService(db)

    with patch.object(
        svc, "_send_system_inbox", side_effect=RuntimeError("boom")
    ):
        # Must not raise — soft-fail contract.
        svc._notify_harvest_completed(
            uuid.uuid4(),
            ore=1,
            precious_metals=0,
            quantum_shards=0,
            harvest_id=uuid.uuid4(),
        )


def test_warn_expiring_licenses_once_per_license():
    db = MagicMock()
    player_id = uuid.uuid4()
    lic_id = uuid.uuid4()
    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    lic = SimpleNamespace(
        id=lic_id,
        player_id=player_id,
        sector_number=42,
        expires_at=(now + timedelta(minutes=30)).replace(tzinfo=None),
    )

    # First query → ClaimLicense rows; second Message dedup → None; then Player for send
    call_n = {"n": 0}

    def query_side_effect(model):
        q = MagicMock()
        name = getattr(model, "__name__", str(model))
        call_n["n"] += 1
        if name == "ClaimLicense":
            q.filter.return_value.all.return_value = [lic]
        elif name == "Message":
            # No prior warning
            q.filter.return_value.first.return_value = None
        elif name == "Player":
            q.filter.return_value.first.return_value = SimpleNamespace(id=player_id)
        else:
            q.filter.return_value.first.return_value = None
            q.filter.return_value.all.return_value = []
        return q

    db.query.side_effect = query_side_effect
    svc = MiningService(db)
    warned = svc.warn_expiring_claim_licenses(now=now)
    assert warned == 1
    assert db.add.called

    # Second pass: Message already exists → no spam
    def query_dedup(model):
        q = MagicMock()
        name = getattr(model, "__name__", str(model))
        if name == "ClaimLicense":
            q.filter.return_value.all.return_value = [lic]
        elif name == "Message":
            q.filter.return_value.first.return_value = (uuid.uuid4(),)
        else:
            q.filter.return_value.first.return_value = None
        return q

    db2 = MagicMock()
    db2.query.side_effect = query_dedup
    svc2 = MiningService(db2)
    assert svc2.warn_expiring_claim_licenses(now=now) == 0
    assert not db2.add.called
