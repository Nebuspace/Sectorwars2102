"""Backfill: re-type class-8 stations mis-typed BLACK_MARKET back to TRADING

WO-P2-econ-blackmarket-venue-spawn Leg B (data-only, follows Leg A's code
fix at ``bang_import_service.py``'s ``_STATION_TYPE_BY_CLASS`` — the
``8: StationType.BLACK_MARKET`` entry conflated ``StationClass.CLASS_8``
("Black Hole", a PRICING tier — premium buyer, ``station_class_map.py``'s
``is_premium_buyer``) with the illegal-goods VENUE TYPE
``contraband_service.py`` gates the contraband-trading kernel on. Confirmed
live on stage before this migration: 393 stations (12.2% of the fleet)
carried ``type = 'BLACK_MARKET'`` — matching bang's port-class roll landing
on 8 (``sw2102-bang/src/content.ts`` rolls ``rangeInt(1, 8)`` inclusive,
~12.5% of randomly-generated ports).

PREDICATE: ``UPDATE stations SET type = 'TRADING' WHERE type = 'BLACK_MARKET'
AND station_class = 'CLASS_8'``. The extra ``station_class = 'CLASS_8'``
clause is not strictly load-bearing today — Leg A's fix confirms
``_STATION_TYPE_BY_CLASS`` was the ONLY writer of ``type = 'BLACK_MARKET'``
anywhere in the codebase (grep-verified: no admin route, no other generator,
no seed script ever sets it) — but it costs nothing and makes the predicate
self-documenting: every row this touches IS explainable by the bug, not just
"every currently-BLACK_MARKET row, trust me".

ORDERING DEPENDENCY (must-read before re-running or reordering this stack):
this migration MUST run strictly AFTER Leg A (the code fix, already shipped —
otherwise a live server would immediately re-mint new mis-typed rows behind
this backfill's back) and STRICTLY BEFORE Leg C (canon-driven INTENTIONAL
BLACK_MARKET venue placement, held/not yet built as of this migration).
``StationClass.CLASS_8`` is NOT how Leg C will place venues — canon places
black-market venues in Frontier-zone / low-security SECTORS, a property of
the sector, not the station's pricing CLASS — so this predicate should stay
disjoint from Leg C's real placements by construction. If a future change
ever has Leg C reuse CLASS_8 stations, this predicate would need
re-checking before any similar backfill runs again. This migration itself
is a point-in-time, run-once data fix, not a recurring sweep — running it
again after Leg A has shipped and stayed shipped is a safe no-op (nothing
will still match the WHERE clause).

DOWNGRADE — INTENTIONALLY IRREVERSIBLE, not a no-op-and-pretend: the naive
reversal (``type = 'TRADING' AND station_class = 'CLASS_8'`` -> back to
BLACK_MARKET) is UNSAFE, not just imprecise. ``nexus_generation_service.py``
independently rolls ``StationClass.CLASS_8`` against a random TYPE pool
(``TRADING``/``INDUSTRIAL``/``DIPLOMATIC``/``SCIENTIFIC``) for its own,
entirely legitimate Central Nexus ports — those class-8 TRADING rows were
NEVER part of this bug and predate this migration. A class-8-scoped
downgrade would silently corrupt them into fake black-market venues, which
is worse than doing nothing (it reintroduces a variant of the exact bug this
migration fixes, on rows that were never broken). This migration records no
per-row "was this one of the 393" marker, so there is no safe way to target
only the originally-mistyped rows on a downgrade. ``downgrade()`` therefore
raises rather than silently no-op-ing — an operator attempting
``alembic downgrade`` here needs to know the data change is NOT reversible
by this migration, not be told it succeeded when nothing (safely) could be
undone.

Revision ID: 9210767b676a
Revises: 40ce9a434884
Create Date: 2026-07-10 00:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '9210767b676a'
down_revision = '40ce9a434884'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE stations
        SET type = 'TRADING'
        WHERE type = 'BLACK_MARKET'
          AND station_class = 'CLASS_8'
        """
    )


def downgrade() -> None:
    # INTENTIONALLY IRREVERSIBLE — see the module docstring's "DOWNGRADE"
    # section. A class-8-scoped reversal would corrupt nexus_generation_
    # service.py's legitimate, pre-existing class-8 TRADING/INDUSTRIAL/
    # DIPLOMATIC/SCIENTIFIC stations (which independently roll CLASS_8
    # against a random type pool, unrelated to this bug) into fake
    # black-market venues. No per-row marker of "was this one of the
    # originally mistyped 393" was recorded, so there is no safe subset to
    # target. Raising (not a silent `pass`) so an operator running
    # `alembic downgrade` here is told plainly that nothing was undone,
    # rather than believing the reversal succeeded.
    raise RuntimeError(
        "9210767b676a is intentionally irreversible -- reversing the "
        "class-8 BLACK_MARKET->TRADING backfill by station_class alone "
        "would corrupt legitimate, unrelated class-8 stations that "
        "nexus_generation_service.py creates independently. See this "
        "migration's module docstring."
    )
