"""Unit coverage for the ARIA narration kernel (WO-ARIA-NARRATE-KERNEL /
ADR-0068): the 5 buildable catalog rows (P-F1, P-F7, P-F8, P-A2, P-A3),
the global narration ceiling + priority-aware backlog queue, per-row
suppression, assistance-level gating, and the zero-LLM pin.

The kernel (src/services/aria_narration_service.py) is entirely DB-free
by design -- see its module docstring -- so most tests here instantiate a
fresh AriaNarrationService() directly: no DB fixture, no asyncio. The one
exception is resolve_assistance_level's single DB read, exercised with a
minimal fake Session that decodes the SUT's real SQLAlchemy filter()
clause (this codebase's mock-only unit-test convention, see
test_aria_market_observation.py).
"""
from __future__ import annotations

import asyncio
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.models.market_transaction import MarketTransaction, TransactionType
from src.services.aria_narration_service import (
    BACKLOG_MAX,
    PRIORITY_P_A,
    PRIORITY_P_F,
    REGISTRY,
    AriaNarrationService,
    NarrationLine,
    dispatch_narration_push,
    resolve_assistance_level,
)

NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
CEILING = timedelta(seconds=60)


def _svc() -> AriaNarrationService:
    return AriaNarrationService()


def _contains_identity(seq, item) -> bool:
    """Identity-based membership check -- NarrationLine is a plain
    (non-frozen) dataclass, so distinct lines from the same event_id with
    the same rendered text/timestamp compare EQUAL by value; only object
    identity distinguishes "this specific call's line" here."""
    return any(x is item for x in seq)


# --------------------------------------------------------------------- #
# Zero-LLM pin
# --------------------------------------------------------------------- #

def test_zero_llm_imports():
    """Source grep -- the kernel must mention no AI-provider symbols
    anywhere (imports, comments, strings). Manual templates only."""
    source = (
        Path(__file__).resolve().parents[2]
        / "src" / "services" / "aria_narration_service.py"
    ).read_text()
    assert re.search(r"anthropic|openai|provider", source, re.IGNORECASE) is None


# --------------------------------------------------------------------- #
# Registry shape pin
# --------------------------------------------------------------------- #

def test_registry_covers_exactly_the_five_buildable_rows():
    assert set(REGISTRY.keys()) == {"P-F1", "P-F7", "P-F8", "P-A2", "P-A3"}
    assert REGISTRY["P-F1"].suppression_scope == "session"
    for event_id in ("P-F7", "P-F8", "P-A2", "P-A3"):
        assert REGISTRY[event_id].suppression_scope == "ever"
    for event_id in ("P-F1", "P-F7", "P-F8"):
        assert REGISTRY[event_id].priority_rank == PRIORITY_P_F
    for event_id in ("P-A2", "P-A3"):
        assert REGISTRY[event_id].priority_rank == PRIORITY_P_A


# --------------------------------------------------------------------- #
# Graceful degradation
# --------------------------------------------------------------------- #

def test_unknown_event_id_returns_none():
    svc = _svc()
    assert svc.record_event("P-NOPE", "player-x", now=NOW) is None


def test_missing_template_context_key_returns_none_gracefully():
    svc = _svc()
    # P-F1's template needs {margin}; omit it -- must not raise.
    assert svc.record_event("P-F1", "player-x", context={}, now=NOW) is None


# --------------------------------------------------------------------- #
# Global ceiling + priority-aware backlog (aria-companion.md:212, 238)
# --------------------------------------------------------------------- #

def test_ceiling_five_events_one_emitted_backlog_capped_oldest_dropped():
    svc = _svc()
    results: List[Any] = []
    for i in range(5):
        line = svc.record_event(
            "P-A2",
            "player-1",
            dedupe_key=f"sector-{i}",
            context={"sector_type_desc": "standard space"},
            now=NOW,
        )
        assert line is not None
        results.append(line)

    emitted_now = [line for line in results if line.delivered_immediately]
    assert len(emitted_now) == 1
    assert emitted_now[0] is results[0]

    assert svc.queue_depth("player-1") == BACKLOG_MAX

    # Drain in FIFO order, advancing past the ceiling each time.
    t = NOW
    drained: List[Any] = []
    for _ in range(BACKLOG_MAX):
        t += CEILING
        popped = svc.drain_due_lines("player-1", now=t)
        assert len(popped) == 1
        drained.append(popped[0])

    # results[0] was emitted immediately (never queued). results[1] was
    # the OLDEST queued entry when results[4] arrived at a full backlog,
    # so it is the one dropped -- results[2..4] survive.
    assert not _contains_identity(drained, results[1])
    assert _contains_identity(drained, results[2])
    assert _contains_identity(drained, results[3])
    assert _contains_identity(drained, results[4])
    assert svc.queue_depth("player-1") == 0


def test_priority_eviction_p_a_dropped_for_incoming_p_f():
    svc = _svc()

    # Burn the immediate-emission slot first so subsequent calls queue.
    burn = svc.record_event(
        "P-A2",
        "player-2",
        dedupe_key="sector-burn",
        context={"sector_type_desc": "standard space"},
        now=NOW,
    )
    assert burn is not None and burn.delivered_immediately

    a_lines: List[Any] = []
    for i in range(BACKLOG_MAX):
        line = svc.record_event(
            "P-A2",
            "player-2",
            dedupe_key=f"sector-{i}",
            context={"sector_type_desc": "standard space"},
            now=NOW,
        )
        assert line is not None
        assert not line.delivered_immediately
        a_lines.append(line)
    assert svc.queue_depth("player-2") == BACKLOG_MAX

    f_line = svc.record_event(
        "P-F8",
        "player-2",
        dedupe_key="Lieutenant",
        context={"new_rank": "Lieutenant", "combat_bonus": 12, "max_turns_bonus": 200},
        now=NOW,
    )
    assert f_line is not None
    assert not f_line.delivered_immediately

    # Queue never grows past the cap, and the incoming P-F row is retained.
    assert svc.queue_depth("player-2") == BACKLOG_MAX

    t = NOW
    drained: List[Any] = []
    for _ in range(BACKLOG_MAX):
        t += CEILING
        popped = svc.drain_due_lines("player-2", now=t)
        assert len(popped) == 1
        drained.append(popped[0])

    drained_event_ids = [line.event_id for line in drained]
    assert drained_event_ids.count("P-F8") == 1
    assert drained_event_ids.count("P-A2") == BACKLOG_MAX - 1
    # The oldest P-A entry (a_lines[0]) was evicted in favor of P-F8.
    assert not _contains_identity(drained, a_lines[0])
    assert _contains_identity(drained, a_lines[1])
    assert _contains_identity(drained, a_lines[2])
    assert _contains_identity(drained, f_line)


# --------------------------------------------------------------------- #
# Per-row suppression -- one test per buildable row
# --------------------------------------------------------------------- #

def test_suppression_p_f1_once_per_session():
    svc = _svc()
    session_a = "2026-07-10T10:00:00+00:00"

    first = svc.record_event(
        "P-F1", "player-3", session_token=session_a,
        context={"margin": "4,200"}, now=NOW,
    )
    assert first is not None

    second = svc.record_event(
        "P-F1", "player-3", session_token=session_a,
        context={"margin": "1,000"}, now=NOW + timedelta(seconds=5),
    )
    assert second is None  # same session -- suppressed

    session_b = "2026-07-10T14:00:00+00:00"
    third = svc.record_event(
        "P-F1", "player-3", session_token=session_b,
        context={"margin": "500"}, now=NOW + timedelta(hours=4),
    )
    assert third is not None  # new session (new last_game_login) -- fires again


def test_suppression_p_f7_once_per_station_ever():
    svc = _svc()

    first = svc.record_event(
        "P-F7", "player-4", dedupe_key="station-A",
        context={"station_name": "Alpha", "contract_count": 3, "plural": "s"},
        now=NOW,
    )
    assert first is not None

    second = svc.record_event(
        "P-F7", "player-4", dedupe_key="station-A",
        context={"station_name": "Alpha", "contract_count": 5, "plural": "s"},
        now=NOW + timedelta(days=1),
    )
    assert second is None  # same station -- suppressed forever

    third = svc.record_event(
        "P-F7", "player-4", dedupe_key="station-B",
        context={"station_name": "Beta", "contract_count": 1, "plural": ""},
        now=NOW,
    )
    assert third is not None  # different station -- fires


def test_suppression_p_f8_once_per_promotion():
    svc = _svc()

    first = svc.record_event(
        "P-F8", "player-5", dedupe_key="Lieutenant",
        context={"new_rank": "Lieutenant", "combat_bonus": 12, "max_turns_bonus": 200},
        now=NOW,
    )
    assert first is not None

    second = svc.record_event(
        "P-F8", "player-5", dedupe_key="Lieutenant",
        context={"new_rank": "Lieutenant", "combat_bonus": 12, "max_turns_bonus": 200},
        now=NOW,
    )
    assert second is None  # same promotion -- suppressed

    third = svc.record_event(
        "P-F8", "player-5", dedupe_key="Commander",
        context={"new_rank": "Commander", "combat_bonus": 16, "max_turns_bonus": 50},
        now=NOW,
    )
    assert third is not None  # next promotion -- fires again


def test_suppression_p_a2_once_per_sector_ever():
    svc = _svc()

    first = svc.record_event(
        "P-A2", "player-6", dedupe_key="sector-42",
        context={"sector_type_desc": "a nebula"}, now=NOW,
    )
    assert first is not None

    second = svc.record_event(
        "P-A2", "player-6", dedupe_key="sector-42",
        context={"sector_type_desc": "a nebula"}, now=NOW,
    )
    assert second is None  # same sector -- suppressed forever

    third = svc.record_event(
        "P-A2", "player-6", dedupe_key="sector-99",
        context={"sector_type_desc": "open void"}, now=NOW,
    )
    assert third is not None  # different sector -- fires


def test_suppression_p_a3_once_per_team_join():
    svc = _svc()

    first = svc.record_event(
        "P-A3", "player-7", dedupe_key="team-nova",
        context={"team_name": "Nova Corp"}, now=NOW,
    )
    assert first is not None

    second = svc.record_event(
        "P-A3", "player-7", dedupe_key="team-nova",
        context={"team_name": "Nova Corp"}, now=NOW,
    )
    assert second is None  # same team -- suppressed (re-joining same team)

    third = svc.record_event(
        "P-A3", "player-7", dedupe_key="team-vega",
        context={"team_name": "Vega Alliance"}, now=NOW,
    )
    assert third is not None  # different team -- fires


# --------------------------------------------------------------------- #
# Assistance-level slicing (aria-companion.md, [NO-CANON] 3-level vocab)
# --------------------------------------------------------------------- #

def test_assistance_minimal_suppresses_atmospheric_but_not_standard():
    svc = _svc()

    blocked_a2 = svc.record_event(
        "P-A2", "player-8", dedupe_key="sector-x",
        assistance_level="minimal",
        context={"sector_type_desc": "a nebula"}, now=NOW,
    )
    assert blocked_a2 is None

    blocked_a3 = svc.record_event(
        "P-A3", "player-8", dedupe_key="team-x",
        assistance_level="minimal",
        context={"team_name": "Nova Corp"}, now=NOW,
    )
    assert blocked_a3 is None

    allowed_f8 = svc.record_event(
        "P-F8", "player-8", dedupe_key="Ensign",
        assistance_level="minimal",
        context={"new_rank": "Ensign", "combat_bonus": 12, "max_turns_bonus": 40},
        now=NOW,
    )
    assert allowed_f8 is not None


def test_assistance_medium_and_full_fire_atmospheric_rows():
    for level in ("medium", "full"):
        svc = _svc()
        allowed = svc.record_event(
            "P-A2", "player-9", dedupe_key=f"sector-{level}",
            assistance_level=level,
            context={"sector_type_desc": "a nebula"}, now=NOW,
        )
        assert allowed is not None, f"assistance_level={level} should fire P-A*"


# --------------------------------------------------------------------- #
# resolve_assistance_level -- the one DB-touching helper
# --------------------------------------------------------------------- #

class _FakeQuery:
    def __init__(self, rows: List[Any]):
        self._rows = rows

    def filter(self, *conditions):
        rows = self._rows
        for cond in conditions:
            key = cond.left.key
            value = cond.right.value
            rows = [r for r in rows if getattr(r, key, None) == value]
        return _FakeQuery(rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, rows: List[Any]):
        self._rows = rows

    def query(self, _model):
        return _FakeQuery(self._rows)

    @contextmanager
    def begin_nested(self):
        # resolve_assistance_level's query is SAVEPOINT-scoped
        # (WO-QTI-PHANTOM-SCHEMA lane c) -- a no-op stand-in is enough
        # here since this fake never fails; the real begin_nested()
        # isolation behavior is proven separately with a real SQLite
        # Session (see TestSavepointIsolation below).
        yield


class _RaisingSession:
    @contextmanager
    def begin_nested(self):
        # Real begin_nested() propagates an exception raised inside the
        # `with` block after rolling back to the savepoint -- this stub
        # matches that (no try/except around yield), so query()'s raise
        # below still reaches resolve_assistance_level's outer except.
        yield

    def query(self, _model):
        raise RuntimeError("boom")


def test_resolve_assistance_level_reads_the_profile_column():
    from src.models.ai_trading import PlayerTradingProfile

    player_id = uuid.uuid4()
    profile = PlayerTradingProfile(player_id=player_id, ai_assistance_level="full")

    assert resolve_assistance_level(_FakeSession([profile]), player_id) == "full"


def test_resolve_assistance_level_defaults_to_medium_when_no_profile():
    assert resolve_assistance_level(_FakeSession([]), uuid.uuid4()) == "medium"


def test_resolve_assistance_level_defaults_to_medium_on_error():
    assert resolve_assistance_level(_RaisingSession(), uuid.uuid4()) == "medium"


# --------------------------------------------------------------------- #
# WO-QTI-PHANTOM-SCHEMA lane c addendum -- SAVEPOINT isolation
# --------------------------------------------------------------------- #
# IMPORTANT SCOPE NOTE: all 5 narration hooks are READ-only against the
# shared session (db.query(...).first()/.count() -- no db.add()/flush()
# anywhere). The addendum's own falsifier language ("a narration WRITE
# that raises") describes the sibling WO-SWEEP-ARIA-MI-COLUMN shape
# (test_aria_mi_column_savepoint.py's db.add()+flush() IntegrityError),
# not this one. Verified empirically before writing this test: a genuine
# SQLite SQL-level failure on a plain db.query() read does NOT poison the
# SQLAlchemy session the way an ORM db.add()+flush() IntegrityError does
# (confirmed live -- a failed raw SELECT leaves a SQLite session fully
# usable with or without begin_nested(); only a failed FLUSH of pending
# ORM state reproduces PendingRollbackError, and none of these hooks ever
# flush anything). The read-side Postgres risk this savepoint still
# defends against ("current transaction is aborted, commands ignored
# until end of transaction block" on ANY failed statement, not just
# writes) is real but SQLite-untestable. So this test proves what SQLite
# CAN prove: begin_nested() is genuinely invoked around the query, and a
# query failure inside it propagates cleanly without leaving the session
# unusable for the caller's subsequent work -- a real regression pin,
# just not literal Postgres-abort-recovery evidence.

class TestSavepointIsolation:
    @pytest.fixture()
    def engine(self):
        eng = create_engine("sqlite:///:memory:")
        MarketTransaction.__table__.create(eng)
        return eng

    def test_begin_nested_is_invoked_around_the_margin_query(self, engine):
        """Structural pin: _first_profitable_trade_margin must go through
        db.begin_nested(), not a bare db.query()."""
        from src.api.routes.trading import _first_profitable_trade_margin

        player = SimpleNamespace(id=uuid.uuid4())
        with Session(engine) as db:
            with patch.object(db, "begin_nested", wraps=db.begin_nested) as spy:
                _first_profitable_trade_margin(db, player, "ORE", 10, 1)
            assert spy.called

    def test_failing_margin_query_does_not_poison_the_outer_session(self, engine):
        """Falsifier: a DB-level failure inside the SAVEPOINT-scoped query
        must not leave the session unusable -- the caller's own subsequent
        work (an unrelated insert + commit) must still succeed."""
        from src.api.routes.trading import _first_profitable_trade_margin

        player_id = uuid.uuid4()
        player = SimpleNamespace(id=player_id)

        with Session(engine) as db:
            db.add(MarketTransaction(
                id=uuid.uuid4(), player_id=player_id,
                transaction_type=TransactionType.BUY, commodity="ORE",
                quantity=10, unit_price=5, total_value=50,
            ))
            db.commit()

            real_query = db.query

            def _boom(model):
                if model is MarketTransaction:
                    raise RuntimeError("simulated DB-level failure")
                return real_query(model)

            with patch.object(db, "query", side_effect=_boom):
                with pytest.raises(RuntimeError):
                    _first_profitable_trade_margin(db, player, "ORE", 10, 1)

            # The falsifier: an unguarded failure would leave the session
            # poisoned and THIS insert+commit (the host route's own later
            # work) would raise.
            db.add(MarketTransaction(
                id=uuid.uuid4(), player_id=player_id,
                transaction_type=TransactionType.SELL, commodity="ORE",
                quantity=1, unit_price=10, total_value=10,
            ))
            db.commit()

            count = (
                db.query(MarketTransaction)
                .filter(MarketTransaction.player_id == player_id)
                .count()
            )
        assert count == 2

    def test_successful_margin_query_unaffected_by_the_savepoint_wrap(self, engine):
        """Happy-path companion pin -- the savepoint wrapping introduces
        no behavior change when nothing raises."""
        from src.api.routes.trading import _first_profitable_trade_margin

        player_id = uuid.uuid4()
        player = SimpleNamespace(id=player_id)

        with Session(engine) as db:
            db.add(MarketTransaction(
                id=uuid.uuid4(), player_id=player_id,
                transaction_type=TransactionType.BUY, commodity="ORE",
                quantity=10, unit_price=5, total_value=50,
            ))
            db.commit()

            margin = _first_profitable_trade_margin(db, player, "ORE", 10, 3)
        assert margin == (10 - 5) * 3


# --------------------------------------------------------------------- #
# Lane C -- WS delivery dispatch (dispatch_narration_push)
# --------------------------------------------------------------------- #

def _line(event_id="P-F1", priority_rank=PRIORITY_P_F, delivered_immediately=True):
    return NarrationLine(
        event_id=event_id,
        player_id="player-ws",
        text="Nice — that's a margin.",
        priority_rank=priority_rank,
        created_at=NOW,
        delivered_immediately=delivered_immediately,
    )


def test_to_payload_matches_the_client_wire_contract():
    line = _line()
    payload = line.to_payload()
    assert payload == {
        "type": "aria_narration",
        "event_id": "P-F1",
        "line": "Nice — that's a margin.",
        "priority": PRIORITY_P_F,
        "ts": NOW.isoformat(),
    }


class TestDispatchNarrationPush:
    """Per this codebase's shared-broadcast-helper testing convention:
    monkeypatch connection_manager itself (not asyncio) to capture calls
    in a sync test, and prove the transport is safe with one asyncio test
    that knocks out connection_manager and confirms the caller (a plain
    sync call, per the hook call sites) is unaffected."""

    def test_pushes_via_connection_manager_when_a_loop_is_running(self):
        player = SimpleNamespace(user_id=uuid.uuid4())
        line = _line()
        sent = AsyncMock()

        async def _run():
            with patch(
                "src.services.websocket_service.connection_manager.send_personal_message",
                sent,
            ):
                dispatch_narration_push(player, line)
                # Let the scheduled create_task actually run.
                await asyncio.sleep(0)

        asyncio.run(_run())

        sent.assert_awaited_once_with(str(player.user_id), line.to_payload())

    def test_no_running_loop_is_swallowed_not_raised(self):
        """Called from a genuinely sync context (no event loop) -- must
        degrade silently, never raise, matching every other WS-dispatch
        helper in this codebase (medal_service, movement_service, etc.)."""
        player = SimpleNamespace(user_id=uuid.uuid4())
        line = _line()
        dispatch_narration_push(player, line)  # no assertion needed: must not raise

    def test_missing_user_id_is_a_silent_noop(self):
        player = SimpleNamespace()  # no user_id attribute at all
        line = _line()
        dispatch_narration_push(player, line)  # must not raise

    def test_connection_manager_failure_never_propagates_to_the_caller(self):
        """The transport-safety falsifier: even if send_personal_message
        itself raises, the caller (a plain sync hook call site) must be
        completely unaffected -- this is what makes the push 'best-effort'
        in the same sense as the rest of lane B."""
        player = SimpleNamespace(user_id=uuid.uuid4())
        line = _line()

        async def _boom(*args, **kwargs):
            raise RuntimeError("socket exploded")

        async def _run():
            with patch(
                "src.services.websocket_service.connection_manager.send_personal_message",
                side_effect=_boom,
            ):
                dispatch_narration_push(player, line)
                # The scheduled task itself may log/raise internally to
                # asyncio's default handler, but the CALLER above must
                # already have returned cleanly by this point.
                await asyncio.sleep(0)

        asyncio.run(_run())  # must not raise out of the caller's own frame
