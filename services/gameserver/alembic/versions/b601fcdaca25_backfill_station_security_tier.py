"""Backfill Station.security tier (WO-STN-SEC-1)

DATA-ONLY migration. ``stations.security`` (JSONB, added by an earlier
schema migration) has had ZERO writers anywhere in the codebase — every
existing row has ``security IS NULL``, which ``Station.security_level``
conservatively reads as tier "none", so the live station-protection combat
gate (``combat_service.py`` ``ERR_DOCKED_SHIP_PROTECTED`` at
``security_rank >= basic``) can never fire against any pre-existing station.
This backfills exactly the same tier-derivation rule the worldgen seeder
(``bang_import_service._derive_station_security_tier``) now applies to every
NEWLY-imported station, so existing and future rows agree:

* Operator-managed regions (``terran_space`` / ``central_nexus``) — CLASS_0
  hub -> "premium" in Central Nexus (Nexus Starport Prime), "standard" in
  Terran Space (Federation Capital station / Earth Station); SpaceDock or
  Tier-A TradeDock hub stations -> "standard"
  (FEATURES/economy/station-protection.md § Security tiers).
* Any station in a frontier/lawless cluster (``cluster_type`` IN
  ``FRONTIER_OUTPOST``/``CONTESTED``) -> "none" ("frontier outposts...
  lawless ports" per canon).
* Everything else -> "basic" — NO-CANON WO-STN-SEC-1 default (canon only
  states "Player-owned stations default to Basic" and is silent on ordinary
  CLASS_1-11 NPC ports in any region); a uniform floor rather than an
  unsupported per-class gradient.

Idempotent: guarded by ``WHERE security IS NULL``, so re-running (or running
against a DB where the application-side seeder has already written some
rows) is a no-op for anything already seeded. Reversible: ``downgrade()``
sets every station's ``security`` back to NULL — a blunt but documented
reset (it cannot distinguish rows this migration touched from rows the
now-live worldgen seeder wrote afterward; that tradeoff is intentional for a
one-shot backfill, not something a real deployment is expected to reverse).

Revision ID: b601fcdaca25
Revises: 2d61e3b17ddd
Create Date: 2026-07-09 23:52:05.000000

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = 'b601fcdaca25'
down_revision = '2d61e3b17ddd'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE stations AS st
        SET security = jsonb_build_object('tier', sub.tier)
        FROM (
            SELECT
                st2.id AS station_id,
                CASE
                    WHEN r.region_type IN ('terran_space', 'central_nexus')
                         AND st2.station_class = 'CLASS_0'
                        THEN CASE WHEN r.region_type = 'central_nexus'
                                  THEN 'premium' ELSE 'standard' END
                    WHEN r.region_type IN ('terran_space', 'central_nexus')
                         AND (st2.is_spacedock IS TRUE
                              OR st2.tradedock_tier = 'A')
                        THEN 'standard'
                    WHEN c.type IN ('FRONTIER_OUTPOST', 'CONTESTED')
                        THEN 'none'
                    ELSE 'basic'
                END AS tier
            FROM stations st2
            LEFT JOIN regions r ON r.id = st2.region_id
            LEFT JOIN sectors s ON s.id = st2.sector_uuid
            LEFT JOIN clusters c ON c.id = s.cluster_id
            WHERE st2.security IS NULL
        ) AS sub
        WHERE st.id = sub.station_id
        """
    )


def downgrade() -> None:
    # Documented blunt reset — see module docstring.
    op.execute("UPDATE stations SET security = NULL")
