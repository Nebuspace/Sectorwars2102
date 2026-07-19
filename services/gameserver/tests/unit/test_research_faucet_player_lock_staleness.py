"""WO-MONEY-STRAGGLER-NAIVE, site 1 of 3 (research faucet) -- identity-map
staleness guard on ``research_service.sweep_research_faucet``'s two bare
Player locks (naive-safe fix: no antecedent mutation between the two Player
reads on the settle spine, per the lead's flush-first trace).

``get_current_player`` (auth/dependencies.py:128) loads the Player row
UNLOCKED on the request's sync session. ``GET /planets/{id}`` -> settle ->
this sweep re-reads the SAME PK on the SAME session. Without
``.populate_existing()`` chained before ``.with_for_update()``, SQLAlchemy's
identity map returns the STALE cached object -- not a fresh re-read -- even
though the row-level lock is genuinely acquired at the DB level. The faucet
then does a live credit/ledger RMW (governed-RP crediting + the copay debit,
``_apply_faucet_copay``) on possibly-stale data: a lost update.

Coverage:
  * ``TestPopulateExistingPresentOnBothPlayerLocksASTGuard`` -- structural
    pin (AST, not string-grep -- immune to the source's own prose mentioning
    "populate_existing") that BOTH full-entity Player lock queries carry
    ``.populate_existing()`` before ``.with_for_update()``, and that the
    sibling owned-planets ``_Planet`` lock (a correctly-immune first-load,
    explicitly DO-NOT-TOUCH per the WO) does not.
  * ``TestSteadyStateDrainPlayerLockGuard`` -- the gold-standard proof: a
    REAL SQLAlchemy identity-map staleness repro (SQLite StaticPool, two
    independent Sessions) against the ACTUAL ``sweep_research_faucet``
    function for the steady-state drain branch. Reuses the exact idiom
    proven in ``test_money_reread_wave_b_mack.py``: monkeypatch the
    module's imported ``Player`` symbol to a minimal SQLite-compatible
    mirror, preload it unlocked on one session, commit a concurrent write
    on another, then prove the guarded re-read observes the FRESH row.
  * ``TestFirstSweepAggregateBranchBehavioralSmoke`` -- the first-sweep
    aggregate branch's OWN Player-lock race window (another sweep stamping
    ``swept_at`` strictly between this branch's unlocked peek and its
    locked re-read) needs genuine cross-transaction interleaving that
    isn't feasible to force deterministically here (SQLite's
    ``with_for_update()`` is a no-op -- there is no real blocking to
    exploit for interleaving). Per the WO's fallback, this instead proves
    the fixed path stays behaviorally correct end-to-end.

No source edits here -- test-file-only. Zero mutating git.
"""
from __future__ import annotations

import ast
import inspect
import math

import sqlalchemy as sa
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

import src.models.planet as planet_module
from src.services import research_service


def _session_factory(base: "declarative_base") -> sessionmaker:
    """A real SQLite engine, StaticPool + check_same_thread=False so multiple
    Session() calls share ONE underlying in-memory DB -- lets a second,
    independently-committing session simulate a genuine concurrent writer.
    Matches test_money_reread_wave_b_mack.py's own convention."""
    engine = sa.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)


# =============================================================================
# Structural guard -- both Player locks carry the fix, the sibling doesn't.
# =============================================================================

class TestPopulateExistingPresentOnBothPlayerLocksASTGuard:
    """AST-based (not regex/string-grep, which would false-match this file's
    own docstring prose) pin on the exact call-chain shape."""

    @staticmethod
    def _locked_query_chains(source: str):
        """Return [(base_class_name_or_None, [method, ...]), ...] for every
        .first()/.all()-terminated db.query(...) chain found in ``source``."""
        tree = ast.parse(source)
        chains = []
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("first", "all")
            ):
                continue
            methods = []
            cur = node
            while (
                isinstance(cur, ast.Call)
                and isinstance(cur.func, ast.Attribute)
                and cur.func.attr != "query"
            ):
                methods.append(cur.func.attr)
                cur = cur.func.value
            base_class = None
            if (
                isinstance(cur, ast.Call)
                and isinstance(cur.func, ast.Attribute)
                and cur.func.attr == "query"
                and cur.args
                and isinstance(cur.args[0], ast.Name)
            ):
                base_class = cur.args[0].id
            chains.append((base_class, list(reversed(methods))))
        return chains

    def test_both_player_locks_guarded_planet_lock_untouched(self):
        source = inspect.getsource(research_service.sweep_research_faucet)
        chains = self._locked_query_chains(source)

        player_locks = [m for base, m in chains if base == "Player" and "with_for_update" in m]
        planet_locks = [m for base, m in chains if base == "_Planet" and "with_for_update" in m]

        assert len(player_locks) == 2, (
            f"expected exactly 2 full-entity Player with_for_update() locks "
            f"(first-sweep aggregate + steady-state drain), found "
            f"{len(player_locks)}: {player_locks!r}"
        )
        for methods in player_locks:
            assert "populate_existing" in methods, (
                f"a Player lock is missing populate_existing(): {methods!r}"
            )
            assert methods.index("populate_existing") < methods.index("with_for_update"), (
                f"populate_existing() must precede with_for_update(): {methods!r}"
            )

        assert len(planet_locks) == 1, (
            f"expected exactly 1 _Planet with_for_update() lock (owned_planets, "
            f"DO NOT TOUCH per the WO), found {len(planet_locks)}: {planet_locks!r}"
        )
        assert "populate_existing" not in planet_locks[0], (
            "the sibling _Planet owned_planets lock must stay untouched -- it "
            "is a correctly-immune first-load, not a re-read of a possibly "
            f"identity-mapped row: {planet_locks[0]!r}"
        )


# =============================================================================
# Site A -- steady-state per-planet drain (research_service.py ~:854-860).
# Gold-standard: REAL SQLAlchemy identity-map staleness repro against the
# ACTUAL sweep_research_faucet function.
# =============================================================================

class TestSteadyStateDrainPlayerLockGuard:
    def test_real_sweep_observes_fresh_player_after_unlocked_preload(self, monkeypatch) -> None:
        Base = declarative_base()

        class MirrorPlayer(Base):
            __tablename__ = "mirror_players_faucet_steady"
            id = sa.Column(sa.Integer, primary_key=True)
            credits = sa.Column(sa.Integer)
            research_ledger = sa.Column(sa.JSON)

        class MirrorPlanet(Base):
            __tablename__ = "mirror_planets_faucet_steady"
            id = sa.Column(sa.Integer, primary_key=True)
            owner_id = sa.Column(sa.Integer)
            active_events = sa.Column(sa.JSON)

        SessionFactory = _session_factory(Base)

        # Owner STARTS at rp=500/credits=1000 with swept_at already present
        # (steady-state, not first-contact) -- the values a route-level
        # unlocked preload would have cached.
        seed = SessionFactory()
        seed.add(MirrorPlayer(
            id=1, credits=1000,
            research_ledger={
                "rp": 500, "insight": 0, "doctrine": 0,
                "unlocked": [research_service.tech_tree.FREE_ROOT_ID],
                "swept_at": "2026-01-01T00:00:00+00:00",
            },
        ))
        seed.commit()
        seed.close()

        monkeypatch.setattr(research_service, "Player", MirrorPlayer)
        # Isolate the Player-lock staleness question from unrelated
        # collaborators: settle_contracts owns a separate contracts
        # subsystem, and _empire_soft_cap queries Planet.structures --
        # neither is what this guard protects. soft_cap=inf reproduces the
        # shipped off-switch exactly (governed delta == raw drained,
        # byte-for-byte).
        monkeypatch.setattr(
            research_service, "settle_contracts",
            lambda db, player, _via_settle=False: False,
        )
        monkeypatch.setattr(research_service, "_empire_soft_cap", lambda db, owner_id: math.inf)

        S = SessionFactory()
        # Same-session UNLOCKED full-ORM preload of the owner -- the exact
        # shape of a request's get_current_player dependency
        # (auth/dependencies.py:128).
        unlocked = S.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        assert unlocked.credits == 1000
        assert unlocked.research_ledger["rp"] == 500

        # A concurrent session drains RP and credits to DIFFERENT values and
        # commits -- e.g. another planet's sweep on the same owner landing
        # between the request's preload and this sweep's own re-read.
        concurrent = SessionFactory()
        row = concurrent.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        row.credits = 5000
        row.research_ledger = {
            "rp": 9000, "insight": 0, "doctrine": 0,
            "unlocked": [research_service.tech_tree.FREE_ROOT_ID],
            "swept_at": "2026-01-01T00:00:00+00:00",
        }
        concurrent.commit()
        concurrent.close()

        held_planet = MirrorPlanet(id=10, owner_id=1, active_events={"research_points": 20})

        # THE ACTUAL FUNCTION UNDER TEST -- not a hand-copied query.
        result = research_service.sweep_research_faucet(S, held_planet, _via_settle=True)
        assert result is True

        # Pre-fix (bare with_for_update(), no populate_existing): the
        # steady-state branch would have read the STALE identity-mapped
        # player (credits=1000, rp=500), governed+credited this sweep's 20
        # raw RP onto the stale rp -> 520, and debited the copay off the
        # stale credits -> 980 -- silently CLOBBERING the concurrent
        # session's fresh rp=9000/credits=5000 on write-back (a lost
        # update). Post-fix: sees the FRESH rp=9000/credits=5000.
        assert unlocked.research_ledger["rp"] == 9020, (
            f"expected the guard to fold this sweep's 20 governed RP onto "
            f"the FRESH rp=9000 (-> 9020), got "
            f"{unlocked.research_ledger['rp']!r}. If this ever reads 520, "
            f"the steady-state Player lock's populate_existing() guard has "
            f"regressed to the pre-fix stale-identity-map lost-update."
        )
        # 20 governed RP -> faucet_copay = floor(0.10 * 20 * 10) = 20cr,
        # debited off the FRESH credits=5000 (not the stale 1000).
        assert unlocked.credits == 4980, (
            f"expected 5000 - 20cr copay = 4980 off the FRESH balance, got "
            f"{unlocked.credits!r}. If this ever reads 980, the fix has "
            f"regressed to a stale-balance debit."
        )
        assert held_planet.active_events["research_points"] == 0

        S.commit()
        check = SessionFactory()
        final = check.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        assert final.research_ledger["rp"] == 9020
        assert final.credits == 4980
        check.close()
        S.close()


class TestSteadyStateBareWithForUpdateReadsStaleSanity:
    """Non-vacuous proof (not just 'the new test passes'): the IDENTICAL
    concurrent-write scenario, but with a hand-copied query that omits
    populate_existing -- the exact pre-fix shape -- DOES return the stale
    row. Confirms this harness is actually capable of detecting the
    regression the guard prevents."""

    def test_bare_with_for_update_without_populate_existing_reads_stale(self) -> None:
        Base = declarative_base()

        class MirrorPlayer(Base):
            __tablename__ = "mirror_players_faucet_steady_inverted"
            id = sa.Column(sa.Integer, primary_key=True)
            credits = sa.Column(sa.Integer)

        SessionFactory = _session_factory(Base)
        seed = SessionFactory()
        seed.add(MirrorPlayer(id=1, credits=1000))
        seed.commit()
        seed.close()

        S = SessionFactory()
        unlocked = S.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        assert unlocked.credits == 1000

        concurrent = SessionFactory()
        row = concurrent.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        row.credits = 5000
        concurrent.commit()
        concurrent.close()

        # The PRE-FIX shape: with_for_update() only, no populate_existing().
        stale = S.query(MirrorPlayer).filter(MirrorPlayer.id == 1).with_for_update().first()
        assert stale is unlocked
        assert stale.credits == 1000, (
            "harness sanity check failed -- without populate_existing() this "
            "must read the STALE identity-mapped value, proving the guard "
            "(not test luck) is what makes the steady-state repro above pass"
        )
        S.close()


# =============================================================================
# Site B -- first-sweep aggregate branch (research_service.py ~:788-796).
# Behavioral smoke (see module docstring for why a real staleness repro of
# THIS branch's own race window isn't feasible synchronously).
# =============================================================================

class TestFirstSweepAggregateBranchBehavioralSmoke:
    def test_aggregate_wipe_and_refund_still_correct_post_fix(self, monkeypatch) -> None:
        Base = declarative_base()

        class MirrorPlayer(Base):
            __tablename__ = "mirror_players_faucet_aggregate"
            id = sa.Column(sa.Integer, primary_key=True)
            credits = sa.Column(sa.Integer)
            research_ledger = sa.Column(sa.JSON)

        class MirrorPlanet(Base):
            __tablename__ = "mirror_planets_faucet_aggregate"
            id = sa.Column(sa.Integer, primary_key=True)
            owner_id = sa.Column(sa.Integer)
            active_events = sa.Column(sa.JSON)

        SessionFactory = _session_factory(Base)
        seed = SessionFactory()
        seed.add(MirrorPlayer(id=2, credits=100, research_ledger=None))  # never swept
        seed.add(MirrorPlanet(id=20, owner_id=2, active_events={"research_points": 30}))
        seed.add(MirrorPlanet(id=21, owner_id=2, active_events={"research_points": 10}))
        seed.commit()
        seed.close()

        monkeypatch.setattr(research_service, "Player", MirrorPlayer)
        # The first-sweep branch's owned-planets query performs a LOCAL
        # `from src.models.planet import Planet as _Planet` at CALL time --
        # patch the module attribute it re-imports fresh each call (not
        # research_service's own module-level `Planet` name, bound once at
        # research_service's import time and never re-read).
        monkeypatch.setattr(planet_module, "Planet", MirrorPlanet)

        S = SessionFactory()
        held_planet = S.query(MirrorPlanet).filter(MirrorPlanet.id == 20).first()

        # THE ACTUAL FUNCTION UNDER TEST.
        result = research_service.sweep_research_faucet(S, held_planet, _via_settle=True)

        assert result is True
        assert held_planet.active_events["research_points"] == 0
        S.commit()

        check = SessionFactory()
        final_player = check.query(MirrorPlayer).filter(MirrorPlayer.id == 2).first()
        # A.4 aggregate refund across BOTH owned planets: (30 + 10) * rate.
        assert final_player.credits == 100 + 40 * research_service.RP_TO_CREDIT_RATE
        assert final_player.research_ledger["rp"] == 0
        assert "swept_at" in final_player.research_ledger
        planet_b = check.query(MirrorPlanet).filter(MirrorPlanet.id == 21).first()
        assert planet_b.active_events["research_points"] == 0
        check.close()
        S.close()
