"""mack -- behavioral gate on WO-MONEY-REREAD-SERVICES Wave-B (UNCOMMITTED):
`.populate_existing()` (or async `.execution_options(populate_existing=True)`)
chained onto `.with_for_update()` at 36 sites across 17 service files
(combat/planetary/citadel/docking/movement/mining/research/ship_upgrade/team/
grey_flag/pioneer/terraforming/fleet/genesis/message_beacon/bounty/
regional_governance). One shared helper (`bounty._load_two_players_for_update`)
was deliberately WITHHELD -- see the CROSS-CHECK class below. No source edits
-- read-only gate, test-file-only. Zero git (beyond the read-only status/diff
already used to identify the diff surface).

CHECK 2 (guard works, real SQLAlchemy) -- reuses the exact idiom proven in
test_money_reread_wave_a_mack.py: monkeypatch the target module's imported
`Player` symbol to a minimal SQLite-compatible mirror class, then call the
REAL function under test directly against a StaticPool SQLite engine shared
by two independent Sessions (so a second session's commit is a genuine
concurrent write, not a same-session mutation). Two representative sites,
chosen to cover both shapes Wave-B guards:
  1. `bounty_service.BountyService.place_bounty` -- a plain scalar column
     (credits), `self.db`-style service.
  2. `research_service.unlock_node` -- a JSONB column (research_ledger),
     module-level-`db`-style service (mirrors message_beacon_service /
     citadel_service / planetary_service's calling convention).
Both prove the SAME mechanism the WO's fix rests on: an unlocked same-session
preload of the SAME PK, followed by the guarded function's own
`.populate_existing().with_for_update()` re-read, observes the value a
CONCURRENT session committed in between -- not the stale identity-mapped
copy a bare `.with_for_update()` (no populate_existing) would have returned.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from src.services import bounty_service, research_service


def _session_factory(base: "declarative_base") -> sessionmaker:
    """A real SQLite engine, StaticPool + check_same_thread=False so multiple
    Session() calls share ONE underlying in-memory DB -- lets a second,
    independently-committing session simulate a genuine concurrent writer.
    Matches test_money_reread_wave_a_mack.py's own convention."""
    engine = sa.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)


# =============================================================================
# Site 1 -- bounty_service.BountyService.place_bounty (bounty_service.py:325)
# Plain scalar credits column; self.db-style service.
# =============================================================================

class TestBountyPlaceBountyGuard:
    def test_real_place_bounty_observes_fresh_credits_after_unlocked_preload(
        self, monkeypatch,
    ) -> None:
        Base = declarative_base()

        class MirrorPlayer(Base):
            __tablename__ = "mirror_players_bounty_svc"
            id = sa.Column(sa.Integer, primary_key=True)
            credits = sa.Column(sa.Integer)
            settings = sa.Column(sa.JSON)
            nickname = sa.Column(sa.String)
            personal_reputation = sa.Column(sa.Integer)

        SessionFactory = _session_factory(Base)
        seed = SessionFactory()
        # Placer STARTS at 2000cr (the value a route-level unlocked preload
        # would have cached) -- enough to cover a 1000cr bounty + 100cr (10%)
        # fee = 1100cr total.
        seed.add(MirrorPlayer(id=1, credits=2000, settings={}, nickname="Placer"))
        seed.add(MirrorPlayer(id=2, credits=0, settings={}, nickname="Target"))
        seed.commit()
        seed.close()

        monkeypatch.setattr(bounty_service, "Player", MirrorPlayer)

        S = SessionFactory()
        # Same-session UNLOCKED full-ORM preload of the placer -- the exact
        # shape of a route's get_current_player dependency, per the WO's own
        # rationale comment at bounty_service.py:320-324.
        unlocked = S.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        assert unlocked.credits == 2000

        # A concurrent session drains the SAME placer's balance to 500cr and
        # commits -- e.g. a race against another purchase. 500cr can no
        # longer cover the 1100cr total_cost.
        concurrent = SessionFactory()
        row = concurrent.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        row.credits = 500
        concurrent.commit()
        concurrent.close()

        # THE ACTUAL FUNCTION UNDER TEST -- not a hand-copied query.
        result = bounty_service.BountyService(S).place_bounty(
            placer_id=1, target_id=2, amount=1000,
        )

        # Pre-fix (bare with_for_update(), no populate_existing): this would
        # have read the STALE identity-mapped credits=2000, PASSED the
        # affordability check, deducted 1100 from the stale copy (writing
        # 900 back over the real fresh 500 -- a lost-update that both
        # invents 400cr AND clobbers the concurrent debit), and returned
        # success=True. Post-fix: sees the FRESH credits=500 and correctly
        # rejects.
        assert result["success"] is False, (
            f"expected the guard to observe FRESH credits=500 (insufficient "
            f"for the 1100cr total) and reject -- got {result!r}. If this "
            f"ever succeeds, place_bounty's populate_existing() guard has "
            f"regressed to the pre-fix stale-identity-map lost-update."
        )
        assert "500" in result["message"]

        # Confirm the row genuinely was NOT mutated (no partial/incorrect
        # deduction landed on the real fresh balance).
        S.commit()
        check = SessionFactory()
        final = check.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        assert final.credits == 500
        check.close()
        S.close()

    def test_inverted_bare_with_for_update_without_populate_existing_reads_stale(
        self, monkeypatch,
    ) -> None:
        """Non-vacuous proof (not just 'the new test passes'): the IDENTICAL
        scenario, but with a hand-copied query that omits populate_existing
        -- the exact pre-fix shape -- DOES return the stale credits=2000.
        Confirms this harness is actually capable of detecting the
        regression the guard prevents, rather than passing regardless of
        whether the guard is present."""
        Base = declarative_base()

        class MirrorPlayer(Base):
            __tablename__ = "mirror_players_bounty_svc_inverted"
            id = sa.Column(sa.Integer, primary_key=True)
            credits = sa.Column(sa.Integer)

        SessionFactory = _session_factory(Base)
        seed = SessionFactory()
        seed.add(MirrorPlayer(id=1, credits=2000))
        seed.commit()
        seed.close()

        S = SessionFactory()
        unlocked = S.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        assert unlocked.credits == 2000

        concurrent = SessionFactory()
        row = concurrent.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        row.credits = 500
        concurrent.commit()
        concurrent.close()

        # The PRE-FIX shape: with_for_update() only, no populate_existing().
        stale = S.query(MirrorPlayer).filter(MirrorPlayer.id == 1).with_for_update().first()
        assert stale is unlocked
        assert stale.credits == 2000, (
            "harness sanity check failed -- without populate_existing() this "
            "must read the STALE identity-mapped value, proving the "
            "guard (not test luck) is what makes the site-1 test above pass"
        )
        S.close()


# =============================================================================
# Site 2 -- research_service.unlock_node (research_service.py:631)
# JSONB column (research_ledger); module-level-db-style service (mirrors the
# citadel_service / planetary_service / message_beacon_service calling
# convention -- `db` passed as a parameter, not `self.db`).
# =============================================================================

class TestResearchUnlockNodeGuard:
    def test_real_unlock_node_observes_fresh_ledger_after_unlocked_preload(
        self, monkeypatch,
    ) -> None:
        Base = declarative_base()

        class MirrorPlayer(Base):
            __tablename__ = "mirror_players_research_svc"
            id = sa.Column(sa.Integer, primary_key=True)
            research_ledger = sa.Column(sa.JSON)

        SessionFactory = _session_factory(Base)

        node_id = "test_node"
        fake_node = {
            "id": node_id,
            "name": "Test Node",
            "prereqs": [research_service.tech_tree.FREE_ROOT_ID],
            "cost": {"rp": 100},
        }
        monkeypatch.setattr(
            research_service.tech_tree, "get_node",
            lambda nid: fake_node if nid == node_id else None,
        )

        # STARTS at rp=500 (plenty for the 100rp node) -- the value an
        # earlier unlocked read on this same session would have cached.
        seed_ledger = {
            "rp": 500, "insight": 0, "doctrine": 0,
            "unlocked": [research_service.tech_tree.FREE_ROOT_ID],
        }
        seed = SessionFactory()
        seed.add(MirrorPlayer(id=1, research_ledger=dict(seed_ledger)))
        seed.commit()
        seed.close()

        monkeypatch.setattr(research_service, "Player", MirrorPlayer)

        S = SessionFactory()
        unlocked = S.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        assert unlocked.research_ledger["rp"] == 500

        # A concurrent session spends this SAME player's RP down to 10
        # (e.g. a race against another unlock) and commits. 10rp can no
        # longer cover the 100rp node.
        concurrent = SessionFactory()
        row = concurrent.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        row.research_ledger = {
            "rp": 10, "insight": 0, "doctrine": 0,
            "unlocked": [research_service.tech_tree.FREE_ROOT_ID],
        }
        concurrent.commit()
        concurrent.close()

        # THE ACTUAL FUNCTION UNDER TEST.
        result = research_service.unlock_node(S, 1, node_id)

        # Pre-fix: bare with_for_update() (no populate_existing) returns the
        # STALE identity-mapped ledger (rp=500), can_unlock's re-check under
        # the lock passes on stale data, the node unlocks and rp is debited
        # to 400 -- overwriting the real fresh rp=10 with a fabricated 400
        # (a 390rp lost-update / double-spend). Post-fix: sees the FRESH
        # rp=10 and correctly rejects with insufficient RP.
        assert result["success"] is False, (
            f"expected the guard to observe FRESH rp=10 (insufficient for "
            f"the 100rp node) and reject -- got {result!r}. If this ever "
            f"succeeds, unlock_node's populate_existing() guard has "
            f"regressed to the pre-fix stale-identity-map lost-update."
        )
        assert "research points" in result["message"].lower()

        S.commit()
        check = SessionFactory()
        final = check.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        assert final.research_ledger["rp"] == 10
        assert node_id not in final.research_ledger["unlocked"]
        check.close()
        S.close()
