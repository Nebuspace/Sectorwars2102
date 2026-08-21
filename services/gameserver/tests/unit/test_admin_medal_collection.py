"""LEG-264 — admin GET player medal collection + view_hidden_medal audit."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.api.routes import medals as medals_routes
from src.services.medal_catalog import CAT_SPECIAL
from src.services.medal_service import MedalService


def test_admin_collection_route_registered():
    paths = {
        getattr(r, "path", None)
        for r in medals_routes.router.routes
        if "GET" in (getattr(r, "methods", None) or set())
    }
    assert "/medals/admin/players/{player_id}/collection" in paths


def test_admin_collection_empty_player():
    player_id = uuid.uuid4()
    player = SimpleNamespace(id=player_id, settings={})
    db = MagicMock()

    def _query(model):
        q = MagicMock()
        q.filter.return_value = q
        q.join.return_value = q
        q.order_by.return_value = q
        name = getattr(model, "__name__", str(model))
        if name == "Player" or model is type(player):
            q.first.return_value = player
            return q
        # PlayerMedal,Medal join path
        q.all.return_value = []
        q.first.return_value = player
        return q

    # First query is Player; second is join
    calls = {"n": 0}

    def query_side_effect(*models):
        calls["n"] += 1
        q = MagicMock()
        q.filter.return_value = q
        q.join.return_value = q
        q.order_by.return_value = q
        if calls["n"] == 1:
            q.first.return_value = player
        else:
            q.all.return_value = []
        return q

    db.query.side_effect = query_side_effect
    result = MedalService(db).admin_get_player_collection(
        player_id, viewing_admin_id=uuid.uuid4()
    )
    assert result["success"] is True
    assert result["items"] == []
    assert result["total"] == 0
    assert result["view_hidden_medal_audits_written"] == 0


def test_admin_collection_writes_view_hidden_audit_when_privacy_blocks():
    player_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    player = SimpleNamespace(
        id=player_id,
        settings={"medal_privacy": {"show_hidden": False}},
    )
    pm = SimpleNamespace(
        id=uuid.uuid4(),
        medal_id="special.orange_cat_society",
        awarded_at=datetime.now(timezone.utc),
        awarded_via="system",
        awarded_by_user_id=None,
        source_event_key="exploration",
        source_combat_log_id=None,
        context_payload={"reason": None},
    )
    medal = SimpleNamespace(
        id="special.orange_cat_society",
        name="Orange Cat Society",
        category=CAT_SPECIAL,
        tier="unique",
        description="hidden",
    )

    calls = {"n": 0}

    def query_side_effect(*models):
        calls["n"] += 1
        q = MagicMock()
        q.filter.return_value = q
        q.join.return_value = q
        q.order_by.return_value = q
        if calls["n"] == 1:
            q.first.return_value = player
        else:
            q.all.return_value = [(pm, medal)]
        return q

    db = MagicMock()
    db.query.side_effect = query_side_effect

    result = MedalService(db).admin_get_player_collection(
        player_id, viewing_admin_id=admin_id
    )
    assert result["success"] is True
    assert result["total"] == 1
    assert result["items"][0]["privacy_overridden"] is True
    assert result["items"][0]["is_hidden_catalog"] is True
    assert result["view_hidden_medal_audits_written"] == 1
    assert db.add.called
    audit_row = db.add.call_args[0][0]
    assert audit_row.action == "view_hidden_medal"


def test_admin_collection_no_audit_when_show_hidden_opt_in():
    player_id = uuid.uuid4()
    player = SimpleNamespace(
        id=player_id,
        settings={"medal_privacy": {"show_hidden": True}},
    )
    pm = SimpleNamespace(
        id=uuid.uuid4(),
        medal_id="special.orange_cat_society",
        awarded_at=datetime.now(timezone.utc),
        awarded_via="admin_grant",
        awarded_by_user_id=uuid.uuid4(),
        source_event_key="admin.grant",
        source_combat_log_id=None,
        context_payload={"reason": "promo"},
    )
    medal = SimpleNamespace(
        id="special.orange_cat_society",
        name="Orange Cat Society",
        category=CAT_SPECIAL,
        tier="unique",
        description="hidden",
    )
    calls = {"n": 0}

    def query_side_effect(*models):
        calls["n"] += 1
        q = MagicMock()
        q.filter.return_value = q
        q.join.return_value = q
        q.order_by.return_value = q
        if calls["n"] == 1:
            q.first.return_value = player
        else:
            q.all.return_value = [(pm, medal)]
        return q

    db = MagicMock()
    db.query.side_effect = query_side_effect
    result = MedalService(db).admin_get_player_collection(
        player_id, viewing_admin_id=uuid.uuid4()
    )
    assert result["items"][0]["privacy_overridden"] is False
    assert result["view_hidden_medal_audits_written"] == 0
    assert result["items"][0]["reason"] == "promo"
    assert result["items"][0]["awarded_by_user_id"] == str(pm.awarded_by_user_id)


def test_player_not_found():
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.first.return_value = None
    db.query.return_value = q
    result = MedalService(db).admin_get_player_collection(
        uuid.uuid4(), viewing_admin_id=uuid.uuid4()
    )
    assert result["success"] is False
