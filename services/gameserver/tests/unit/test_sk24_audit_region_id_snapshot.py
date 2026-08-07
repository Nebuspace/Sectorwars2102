"""WO-FIX-AUDIT-TABLES-REGION-ID-SNAPSHOT-COVERAGE — ADR-0050 SK24.

DB-free: assert the five remaining audit tables expose region_id_snapshot.
"""
from __future__ import annotations

from src.models.aria_personal_intelligence import ARIATradingObservation
from src.models.bounty_claim import BountyClaim
from src.models.market_transaction import MarketTransaction
from src.models.npc_character import NPCDeathLog
from src.models.pirate_kill_log import PirateKillLog


def test_sk24_audit_tables_have_region_id_snapshot():
    for model in (
        MarketTransaction,
        BountyClaim,
        NPCDeathLog,
        ARIATradingObservation,
        PirateKillLog,
    ):
        col = model.__table__.c.region_id_snapshot
        assert col.nullable is True, model.__tablename__
        # No FK — plain UUID snapshot (must survive region delete).
        assert col.foreign_keys == set(), model.__tablename__


def test_pirate_kill_log_region_fk_is_set_null():
    col = PirateKillLog.__table__.c.region_id
    assert col.nullable is True
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].ondelete == "SET NULL"
