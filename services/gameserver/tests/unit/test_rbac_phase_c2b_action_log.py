"""RBAC Phase C2b — deferred mutation AdminActionLog wiring.

ACCEPT: log before FIRST call to any internally-committing service;
same-txn atomicity; single-writer; actor=Depends admin; no secrets.
"""

from __future__ import annotations

import inspect

from src.auth.admin_scopes import (
    GALAXY_MANAGE,
    PLAYERS_ADJUST_CREDITS,
    PLAYERS_ADJUST_REP,
    SHIPS_MANAGE,
)


def _assert_log_before(fn, *, marker: str, action: str):
    src = inspect.getsource(fn)
    assert "log_admin_action" in src, fn.__name__
    assert f'action="{action}"' in src, fn.__name__
    assert src.index("log_admin_action") < src.index(marker), (
        f"{fn.__name__}: log must precede {marker!r}"
    )


def test_c2b_events_and_game_events_log_before_commit():
    from src.api.routes import events as ev
    from src.api.routes import admin as admin_mod

    for fn, action in [
        (ev.create_event, "event_create"),
        (ev.update_event, "event_update"),
        (ev.activate_event, "event_activate"),
        (ev.deactivate_event, "event_deactivate"),
        (ev.delete_event, "event_delete"),
        (admin_mod.create_game_event, "game_event_create"),
        (admin_mod.update_game_event, "game_event_update"),
        (admin_mod.activate_game_event, "game_event_activate"),
        (admin_mod.deactivate_game_event, "game_event_deactivate"),
        (admin_mod.delete_game_event, "game_event_delete"),
    ]:
        _assert_log_before(fn, marker="db.commit()", action=action)


def test_c2b_factions_log_before_service_or_commit():
    from src.api.routes import admin_factions as fac

    _assert_log_before(fac.create_faction, marker="db.commit()", action="faction_create")
    _assert_log_before(fac.update_faction, marker="db.commit()", action="faction_update")
    _assert_log_before(fac.delete_faction, marker="db.commit()", action="faction_delete")
    _assert_log_before(
        fac.update_faction_territory,
        marker="await service.update_faction_territory",
        action="faction_territory_update",
    )
    _assert_log_before(
        fac.update_player_reputation,
        marker="await service.update_reputation",
        action="faction_reputation_update",
    )
    assert "PLAYERS_ADJUST_REP" in inspect.getsource(fac.update_player_reputation)


def test_c2b_translation_logs_before_service():
    from src.api.routes import translation as tr

    _assert_log_before(
        tr.set_translation, marker="await translation_service.set_translation", action="translation_set"
    )
    _assert_log_before(
        tr.bulk_import_translations,
        marker="await translation_service.bulk_import_translations",
        action="translation_bulk_import",
    )
    _assert_log_before(
        tr.initialize_translation_data,
        marker="await translation_service.initialize_default_data",
        action="translation_initialize",
    )


def test_c2b_analytics_logs_before_service():
    from src.api.routes import admin_comprehensive as comp

    _assert_log_before(
        comp.create_analytics_snapshot,
        marker="AnalyticsService(",
        action="analytics_snapshot",
    )


def test_c2b_drones_ships_colonization_bulk_nexus():
    from src.api.routes import admin_drones as drones
    from src.api.routes import admin_colonization as col
    from src.api.routes import admin_comprehensive as comp
    from src.api.routes import nexus

    for fn, action in [
        (drones.update_drone, "drone_update"),
        (drones.delete_drone, "drone_delete"),
        (drones.restore_destroyed_drone, "drone_restore"),
    ]:
        _assert_log_before(fn, marker="await db.commit()", action=action)
    _assert_log_before(
        drones.force_recall_drone,
        marker="await service.recall_drone",
        action="drone_force_recall",
    )

    _assert_log_before(
        col.tick_planet_production, marker="changed = settle(", action="planet_tick"
    )
    _assert_log_before(
        comp.create_players_from_all_users, marker="db.commit()", action="player_create_bulk"
    )
    _assert_log_before(
        nexus.generate_central_nexus, marker="background_tasks.add_task", action="nexus_generate_start"
    )


def test_c2b_single_writer_still_holds():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "src"
    offenders = []
    for path in root.rglob("*.py"):
        if path.name in {"admin_action_log.py", "admin_action_log_service.py"}:
            continue
        if "AdminActionLog(" in path.read_text():
            offenders.append(str(path.relative_to(root)))
    assert offenders == []


def test_c2b_e2e_rollback_on_create_bulk_pattern():
    """Extend E2E: bulk-style mutate+log rollback → zero of BOTH."""
    import uuid
    from types import SimpleNamespace

    from sqlalchemy import Column, Integer, String, create_engine, event, text
    from sqlalchemy.orm import declarative_base, sessionmaker
    from sqlalchemy.pool import StaticPool

    from src.services.admin_action_log_service import log_admin_action

    Base = declarative_base()

    class BulkRow(Base):
        __tablename__ = "c2b_bulk_players"
        id = Column(String(36), primary_key=True)
        credits = Column(Integer, default=10000)

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    BulkRow.__table__.create(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE admin_action_logs ("
                "id CHAR(32) PRIMARY KEY, admin_user_id CHAR(32),"
                "scope_used VARCHAR(120), action VARCHAR(200) NOT NULL,"
                "target_type VARCHAR(100), target_id VARCHAR(255),"
                "payload_snapshot TEXT, result VARCHAR(50),"
                "failure_reason TEXT, reviewed_by CHAR(32),"
                "reviewed_at TIMESTAMP, at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
        )
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    actor = SimpleNamespace(id=uuid.uuid4())
    pid = str(uuid.uuid4())
    db.add(BulkRow(id=pid, credits=10000))
    log_admin_action(
        db,
        actor=actor,
        scope_used=PLAYERS_ADJUST_CREDITS,
        action="player_create_bulk",
        target_type="player",
        target_id="bulk",
        payload={"created_count": 1},
    )
    db.rollback()
    assert db.execute(text("SELECT COUNT(*) FROM c2b_bulk_players")).scalar() == 0
    assert db.execute(text("SELECT COUNT(*) FROM admin_action_logs")).scalar() == 0
    db.close()
    engine.dispose()
