"""aria_personal_memories.memory_type canon rename

WO-P6-aria-data-index-registry Lane C (MAX-RULED rename, follow-up to
26ea004450dc): 26ea004450dc's Lane C left aria_personal_memories.memory_type
untouched because two of the three literals actually written
("combat"/"exploration") didn't byte-match any `aria_data_streams` registry
key. Max has since ruled: rename the write-site literals to their canon
registry keys rather than leave the mismatch standing. Of the three:

  * "combat"      -> "threat.combat"   (registry key exists, ARIAPersonalMemory
                                          -backed -- clean rename)
  * "exploration" -> "nav.sector_visit" (registry key exists but is NOT
                                          ARIAPersonalMemory-backed in the
                                          registry -- renamed anyway per Max's
                                          ruling since this write site,
                                          record_exploration_memory, is DEAD
                                          CODE, 0 callers; a purely cosmetic
                                          consistency rename, zero live rows
                                          affected)
  * "market"      -> UNCHANGED. No ARIAPersonalMemory-backed registry key
                      exists for either "market" write site (the significant-
                      price-change alert in record_market_observation, or the
                      trade-completion memory in record_trade_memory_sync --
                      the latter LIVE, wired from trading.py). commerce.trade
                      is the closest conceptual match but its registry
                      storage_table is ARIATradingObservation, not
                      ARIAPersonalMemory (ARIATradingObservation already gets
                      its own separate row via record_trade_observation in
                      the same hook). Per the WO's explicit "STOP and report
                      the mismatch, don't invent a mapping" instruction,
                      "market" is intentionally left as-is here -- flagged in
                      the dispatch report, not silently resolved.

This is a DATA migration on aria_personal_memories.memory_type (String(50),
no enum/check constraint -- see models/aria_personal_intelligence.py) --
not a schema change. Idempotent: each UPDATE's WHERE clause only matches
rows still holding the pre-rename literal, so re-running on already-migrated
rows is a no-op. Downgrade reverses both renames symmetrically.

Revision ID: 40ce9a434884
Revises: 26ea004450dc
Create Date: 2026-07-10 17:49:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '40ce9a434884'
down_revision = '26ea004450dc'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE aria_personal_memories SET memory_type = 'threat.combat' "
        "WHERE memory_type = 'combat'"
    )
    op.execute(
        "UPDATE aria_personal_memories SET memory_type = 'nav.sector_visit' "
        "WHERE memory_type = 'exploration'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE aria_personal_memories SET memory_type = 'exploration' "
        "WHERE memory_type = 'nav.sector_visit'"
    )
    op.execute(
        "UPDATE aria_personal_memories SET memory_type = 'combat' "
        "WHERE memory_type = 'threat.combat'"
    )
