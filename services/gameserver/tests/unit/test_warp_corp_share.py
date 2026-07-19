"""WO-GWQ-WARPSHARE — corp/team warp-knowledge propagation (ADR-0045) plus
the ADR-0064 R-V3 Nexus-warp marker.

DB-free: hand-built fakes, no real DB/app (mirrors test_formation_knowledge.py
/ test_warp_gate_toll.py). ``PlayerWarpKnowledge`` rows are real, unattached
ORM instances (constructor-only — column ``default=`` never applies without a
real flush, so every constructed row explicitly sets ``visibility_state``).
The fake sessions' ``.filter()`` walks the REAL SQLAlchemy
``BinaryExpression``/``BooleanClause
List`` objects the services build (``.left.key`` / ``.right.value`` /
``.operator``), so ``==`` vs ``!=`` and ``or_()`` groups are interpreted
correctly rather than assumed-equality (a small hardening over the sibling
files' convention, verified against a live SQLAlchemy probe at authoring
time: ``or_()`` exposes ``.clauses``/``.operator is operator.or_``,
``!=`` exposes ``.operator is operator.ne``).

PREMISE VERDICT (resolved before building — see STATUS report for the full
grep evidence): ``movement_service._reveal_warp_to_player`` was ALREADY the
live discoverer-write path (single call site, ``scan_for_latent_tunnels``) —
the dormant-with-zero-consumers claim was stale. This WO's actual gap was the
CORP_SHARE fan-out (enum member existed, zero writers) and the Nexus-marker
read (``Region.nexus_warp_sector`` written at bang-import, read by nothing).

Acceptance-criteria map (WO-GWQ-WARPSHARE, 6 total):
  1  TestTeamPropagation::test_team_members_get_corp_share_rows_non_members_unaffected
  2  TestIdempotentReveal::test_rereveal_adds_no_duplicate_rows
  3  TestSoloPlayer::test_solo_player_writes_only_own_row
  4  TestCorpShareHasWriters::test_corp_share_is_written_in_movement_service
  5  TestNexusMarker (REFRAMED — see its class docstring: ADR-0064 R-V3's own
     filter algorithm is personal-discovery-based, not a tier read; the
     "paid tier sees it / free tier doesn't" framing in the dispatch is the
     algorithm's headline CONSEQUENCE, not its mechanism)
  6  TestNoPaymentCodeTouched (source-scoped; also see the diff file list in
     the STATUS report)

Plus two supporting tests beyond the numbered 6, exercising machinery the
acceptance criteria imply but don't separately number: TestConcurrentTeam
mateRace (the SAVEPOINT/IntegrityError path actually fires) and TestProvenance
Upgrade (the CORP_SHARE -> personal upgrade the Nexus-marker's "regardless of
corp share" rule depends on staying correct over time).
"""
from __future__ import annotations

import operator as _op
import pathlib
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, List

from sqlalchemy.exc import IntegrityError

from src.models.player_warp_knowledge import (
    PlayerWarpKnowledge,
    WarpLayer,
    WarpVisibilityState,
    WarpRevealedVia,
)
from src.models.region import Region
from src.models.sector import Sector
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus, WarpTunnelType
from src.services import movement_service as ms
from src.services import quantum_service as qs


# --- shared helpers -------------------------------------------------------- #


def make_player(*, team_id: Any = None, current_region_id: Any = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), user_id=uuid.uuid4(),
        team_id=team_id, current_region_id=current_region_id,
    )


def make_team_member(team_id: Any, player_id: Any) -> SimpleNamespace:
    return SimpleNamespace(team_id=team_id, player_id=player_id)


def make_tunnel_stub() -> SimpleNamespace:
    """A bare tunnel identity for the movement-service tests — nothing under
    test there reads any field but ``id``/``origin_sector_id``/
    ``destination_sector_id`` (the latter two only inside the WS-dispatch
    branch, which is unreachable in a sync test — no running event loop)."""
    return SimpleNamespace(
        id=uuid.uuid4(), origin_sector_id=uuid.uuid4(), destination_sector_id=uuid.uuid4(),
    )


def _cond_kv(cond: Any):
    left = getattr(cond, "left", None)
    right = getattr(cond, "right", None)
    return getattr(left, "key", None), getattr(right, "value", None), getattr(cond, "operator", _op.eq)


# --- movement_service fakes ------------------------------------------------ #


class _FakeKnowledgeQuery:
    """Stands in for ``db.query(PlayerWarpKnowledge)`` — same shape for both
    the discoverer's own lookup and every teammate lookup (player_id /
    warp_layer / warp_id, all equality)."""

    def __init__(self, rows: List[PlayerWarpKnowledge]) -> None:
        self._rows = rows
        self._eq: dict = {}

    def filter(self, *conds: Any) -> "_FakeKnowledgeQuery":
        for cond in conds:
            key, val, op = _cond_kv(cond)
            if key is not None and op is _op.eq:
                self._eq[key] = val
        return self

    def first(self):
        for row in self._rows:
            if all(getattr(row, k, object()) == v for k, v in self._eq.items()):
                return row
        return None


class _FakeScalarTeamIdQuery:
    """Stands in for ``db.query(Player.team_id).filter(Player.id == x).scalar()``."""

    def __init__(self, players: List[SimpleNamespace]) -> None:
        self._players = players
        self._id = None

    def filter(self, *conds: Any) -> "_FakeScalarTeamIdQuery":
        for cond in conds:
            key, val, op = _cond_kv(cond)
            if key == "id" and op is _op.eq:
                self._id = val
        return self

    def scalar(self):
        for p in self._players:
            if p.id == self._id:
                return p.team_id
        return None


class _FakeTeammateJoinQuery:
    """Stands in for ``db.query(Player.id, Player.user_id)
    .join(TeamMember, TeamMember.player_id == Player.id)
    .filter(TeamMember.team_id == team_id, Player.id != discoverer_id).all()``.
    ``.join(...)`` is a no-op (the join target is fixed by the code under
    test); ``.filter()`` distinguishes ``team_id ==`` from ``id !=`` by
    operator, not just key, so it can't be fooled by a stray equality on the
    same column name."""

    def __init__(self, players: List[SimpleNamespace], team_members: List[SimpleNamespace]) -> None:
        self._players = players
        self._team_members = team_members
        self._team_id = None
        self._exclude_id = None

    def join(self, *a: Any, **k: Any) -> "_FakeTeammateJoinQuery":
        return self

    def filter(self, *conds: Any) -> "_FakeTeammateJoinQuery":
        for cond in conds:
            key, val, op = _cond_kv(cond)
            if key == "team_id" and op is _op.eq:
                self._team_id = val
            elif key == "id" and op is _op.ne:
                self._exclude_id = val
        return self

    def all(self):
        member_ids = {tm.player_id for tm in self._team_members if tm.team_id == self._team_id}
        return [
            (p.id, p.user_id) for p in self._players
            if p.id in member_ids and p.id != self._exclude_id
        ]


class _FakeMoveSession:
    """Minimal in-memory Session stand-in for movement_service's warp-share
    functions. ``rows`` is the durable "committed" store; ``add()`` stages,
    ``flush()``/``commit()`` (commit flushes first, mirroring real SQLAlchemy)
    is where a UNIQUE violation actually surfaces. ``_race_on_player_id``, when
    set, simulates a concurrent session's INSERT for that exact
    (player, tunnel) landing between the pre-check and our own flush — mirrors
    test_formation_knowledge.py's ``race_on_formation_id``."""

    def __init__(
        self,
        *,
        players: List[SimpleNamespace],
        team_members: List[SimpleNamespace],
        knowledge_rows: List[PlayerWarpKnowledge] = None,
    ) -> None:
        self.players = players
        self.team_members = team_members
        self.rows: List[PlayerWarpKnowledge] = list(knowledge_rows or [])
        self._pending: List[Any] = []
        self._race_on_player_id: Any = None
        self.flush_count = 0
        self.committed = False

    def query(self, *args: Any) -> Any:
        if len(args) == 1 and args[0] is PlayerWarpKnowledge:
            return _FakeKnowledgeQuery(self.rows)
        if len(args) == 1 and getattr(args[0], "key", None) == "team_id":
            return _FakeScalarTeamIdQuery(self.players)
        if len(args) == 2 and [getattr(a, "key", None) for a in args] == ["id", "user_id"]:
            return _FakeTeammateJoinQuery(self.players, self.team_members)
        raise AssertionError(f"unexpected query args {args!r}")

    def add(self, obj: Any) -> None:
        self._pending.append(obj)

    def flush(self) -> None:
        self.flush_count += 1
        still_pending = []
        for obj in self._pending:
            if isinstance(obj, PlayerWarpKnowledge):
                if obj.player_id == self._race_on_player_id:
                    self._race_on_player_id = None  # only races once
                    winner = PlayerWarpKnowledge(
                        player_id=obj.player_id, warp_layer=obj.warp_layer,
                        warp_id=obj.warp_id, visibility_state=obj.visibility_state,
                        revealed_via=obj.revealed_via,
                    )
                    self.rows.append(winner)
                    self._pending = []
                    raise IntegrityError("INSERT", {}, Exception("duplicate key value violates unique constraint"))
                dup = any(
                    r.player_id == obj.player_id and r.warp_id == obj.warp_id
                    for r in self.rows
                )
                if dup:
                    self._pending = []
                    raise IntegrityError("INSERT", {}, Exception("duplicate key value violates unique constraint"))
                self.rows.append(obj)
            else:
                still_pending.append(obj)
        self._pending = still_pending

    @contextmanager
    def begin_nested(self):
        yield

    def commit(self) -> None:
        self.flush()
        self.committed = True


# --- Accept #1: team propagation, non-members unaffected ------------------ #


class TestTeamPropagation:
    def test_team_members_get_corp_share_rows_non_members_unaffected(self):
        team_id = uuid.uuid4()
        player_a = make_player(team_id=team_id)
        player_b = make_player(team_id=team_id)
        player_c = make_player(team_id=team_id)
        player_d = make_player(team_id=None)  # not on the team at all

        team_members = [
            make_team_member(team_id, player_a.id),
            make_team_member(team_id, player_b.id),
            make_team_member(team_id, player_c.id),
        ]
        db = _FakeMoveSession(players=[player_a, player_b, player_c, player_d], team_members=team_members)
        tunnel = make_tunnel_stub()

        ms._reveal_warp_to_player(db, player_a.id, tunnel, WarpRevealedVia.SCAN)
        db.commit()

        assert len(db.rows) == 3  # A (discoverer) + B + C; D excluded entirely
        a_row = next(r for r in db.rows if r.player_id == player_a.id)
        b_row = next(r for r in db.rows if r.player_id == player_b.id)
        c_row = next(r for r in db.rows if r.player_id == player_c.id)
        assert a_row.revealed_via == WarpRevealedVia.SCAN
        assert b_row.revealed_via == WarpRevealedVia.CORP_SHARE
        assert c_row.revealed_via == WarpRevealedVia.CORP_SHARE
        assert b_row.warp_id == tunnel.id and c_row.warp_id == tunnel.id
        assert b_row.warp_layer == WarpLayer.WARP_TUNNELS
        # D (non-member) is provably unaffected — no row at all.
        assert all(r.player_id != player_d.id for r in db.rows)


# --- Accept #2: re-reveal adds no duplicates ------------------------------- #


class TestIdempotentReveal:
    def test_rereveal_adds_no_duplicate_rows(self):
        team_id = uuid.uuid4()
        player_a = make_player(team_id=team_id)
        player_b = make_player(team_id=team_id)
        team_members = [make_team_member(team_id, player_a.id), make_team_member(team_id, player_b.id)]
        db = _FakeMoveSession(players=[player_a, player_b], team_members=team_members)
        tunnel = make_tunnel_stub()

        ms._reveal_warp_to_player(db, player_a.id, tunnel, WarpRevealedVia.SCAN)
        db.commit()
        # Direct re-invocation (stronger than relying solely on scan_for_
        # latent_tunnels's upstream _player_knows_warp guard) — the
        # propagation step itself must be idempotent too.
        ms._reveal_warp_to_player(db, player_a.id, tunnel, WarpRevealedVia.SCAN)
        db.commit()

        assert sum(1 for r in db.rows if r.player_id == player_a.id) == 1
        assert sum(1 for r in db.rows if r.player_id == player_b.id) == 1
        assert len(db.rows) == 2


# --- Accept #3: solo player writes only own row ---------------------------- #


class TestSoloPlayer:
    def test_solo_player_writes_only_own_row(self):
        player_a = make_player(team_id=None)
        db = _FakeMoveSession(players=[player_a], team_members=[])
        tunnel = make_tunnel_stub()

        ms._reveal_warp_to_player(db, player_a.id, tunnel, WarpRevealedVia.SCAN)
        db.commit()

        assert len(db.rows) == 1
        assert db.rows[0].player_id == player_a.id
        assert db.rows[0].revealed_via == WarpRevealedVia.SCAN


# --- Supporting: SAVEPOINT/IntegrityError race never escapes --------------- #


class TestConcurrentTeammateRace:
    def test_concurrent_share_race_no_integrity_error_escapes(self):
        team_id = uuid.uuid4()
        player_a = make_player(team_id=team_id)
        player_b = make_player(team_id=team_id)
        team_members = [make_team_member(team_id, player_a.id), make_team_member(team_id, player_b.id)]
        db = _FakeMoveSession(players=[player_a, player_b], team_members=team_members)
        tunnel = make_tunnel_stub()
        db._race_on_player_id = player_b.id  # B's INSERT "loses" the race once

        # Must not raise — the SAVEPOINT-scoped insert catches the
        # IntegrityError and treats it as an already-shared no-op.
        ms._reveal_warp_to_player(db, player_a.id, tunnel, WarpRevealedVia.SCAN)
        db.commit()

        assert sum(1 for r in db.rows if r.player_id == player_b.id) == 1


# --- Supporting: personal discovery upgrades a corp-shared row's provenance #


class TestProvenanceUpgrade:
    def test_personal_scan_upgrades_existing_corp_share_row(self):
        """ADR-0064 R-V3's Nexus-marker filter requires PERSONAL discovery
        "regardless of corp share" — this only stays correct over time if a
        corp-shared row upgrades to personal provenance the moment the same
        player later scans the tunnel themselves. See TestNexusMarker."""
        team_id = uuid.uuid4()
        player_a = make_player(team_id=team_id)
        player_b = make_player(team_id=team_id)
        team_members = [make_team_member(team_id, player_a.id), make_team_member(team_id, player_b.id)]
        tunnel = make_tunnel_stub()
        # B holds only a corp-shared row. A already personally knows it too
        # (isolates this test to B's own upgrade — otherwise B's re-reveal
        # would ALSO legitimately fan a fresh CORP_SHARE row out to A, which
        # is exercised separately by TestTeamPropagation).
        b_pre_existing = PlayerWarpKnowledge(
            player_id=player_b.id, warp_layer=WarpLayer.WARP_TUNNELS, warp_id=tunnel.id,
            visibility_state=WarpVisibilityState.REVEALED, revealed_via=WarpRevealedVia.CORP_SHARE,
        )
        a_pre_existing = PlayerWarpKnowledge(
            player_id=player_a.id, warp_layer=WarpLayer.WARP_TUNNELS, warp_id=tunnel.id,
            visibility_state=WarpVisibilityState.REVEALED, revealed_via=WarpRevealedVia.SCAN,
        )
        db = _FakeMoveSession(
            players=[player_a, player_b], team_members=team_members,
            knowledge_rows=[b_pre_existing, a_pre_existing],
        )

        ms._reveal_warp_to_player(db, player_b.id, tunnel, WarpRevealedVia.SCAN)
        db.commit()

        b_row = next(r for r in db.rows if r.player_id == player_b.id)
        assert b_row.revealed_via == WarpRevealedVia.SCAN
        assert b_row.visibility_state == WarpVisibilityState.REVEALED
        assert len(db.rows) == 2  # B's row upgraded in place; A's untouched — no duplicate


# --- Accept #4: CORP_SHARE now has a writer -------------------------------- #


class TestCorpShareHasWriters:
    _MOVEMENT_SERVICE_PATH = pathlib.Path(ms.__file__)

    def test_corp_share_is_written_in_movement_service(self):
        """Pinned regression: at WO authoring time, ``WarpRevealedVia.
        CORP_SHARE`` was defined in the enum and referenced ONLY by the model
        file and its own migration (zero writers, confirmed by a repo-wide
        grep). movement_service.py now writes it."""
        source = self._MOVEMENT_SERVICE_PATH.read_text()
        assert "revealed_via=WarpRevealedVia.CORP_SHARE" in source
        assert "_propagate_warp_reveal_to_team" in source


# --- Accept #5 (REFRAMED): the Nexus-warp marker --------------------------- #


def make_region(*, nexus_warp_sector: Any = None, is_central_nexus: bool = False) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), nexus_warp_sector=nexus_warp_sector, is_central_nexus=is_central_nexus)


def make_sector_stub(*, region_id: Any, sector_number: int, sector_id: int) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), region_id=region_id, sector_number=sector_number, sector_id=sector_id)


def make_tunnel(
    *, origin_sector_id: Any, destination_sector_id: Any,
    status: WarpTunnelStatus = WarpTunnelStatus.ACTIVE, type_: WarpTunnelType = WarpTunnelType.NATURAL,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), origin_sector_id=origin_sector_id,
        destination_sector_id=destination_sector_id, status=status, type=type_,
    )


def _match(row: Any, cond: Any) -> bool:
    clauses = getattr(cond, "clauses", None)
    if clauses is not None:
        results = [_match(row, c) for c in clauses]
        if getattr(cond, "operator", None) is _op.or_:
            return any(results)
        return all(results)
    key, val, op = _cond_kv(cond)
    if key is None:
        return True
    actual = getattr(row, key, object())
    return actual != val if op is _op.ne else actual == val


class _FakeGenericQuery:
    def __init__(self, rows: List[Any]) -> None:
        self._rows = rows
        self._conds: List[Any] = []

    def filter(self, *conds: Any) -> "_FakeGenericQuery":
        self._conds.extend(conds)
        return self

    def first(self):
        for row in self._rows:
            if all(_match(row, c) for c in self._conds):
                return row
        return None

    def all(self):
        return [row for row in self._rows if all(_match(row, c) for c in self._conds)]


class _FakeQSession:
    """Stands in for ``db.query(Region|Sector|WarpTunnel|PlayerWarpKnowledge)``
    inside ``_resolve_nexus_warp_marker`` — a single generic query fake since
    every one of that function's queries is a plain conjunction of equalities
    (plus one ``or_()`` pair for the tunnel lookup, handled by ``_match``)."""

    def __init__(self, *, regions=(), sectors=(), tunnels=(), knowledge=()) -> None:
        self._by_model = {
            Region: list(regions),
            Sector: list(sectors),
            WarpTunnel: list(tunnels),
            PlayerWarpKnowledge: list(knowledge),
        }

    def query(self, model: Any) -> Any:
        try:
            rows = self._by_model[model]
        except KeyError:
            raise AssertionError(f"unexpected query for {model!r}")
        return _FakeGenericQuery(rows)


class TestNexusMarker:
    """Accept #5, REFRAMED. The dispatch's "marker present for paid tier,
    absent for free tier" framing does not match ADR-0064 R-V3's own filter
    algorithm (0064-group-j-worldgen-pipeline-cleanup.md § R-V3): the actual
    gate is PERSONAL PlayerWarpKnowledge discovery, "regardless of corp
    share" — no subscription-tier field is read anywhere. The ADR's own
    text explains the tier angle is a CONSEQUENCE of this rule (a free-tier
    player who never personally scans can't piggyback on a citizen corp-
    mate's share), not a second, independent tier check. These tests pin the
    algorithm actually shipped; see the STATUS report for the full citation.
    """

    def _scenario(self):
        region = make_region(nexus_warp_sector=42)
        nexus_region = make_region(is_central_nexus=True)
        landing = make_sector_stub(region_id=region.id, sector_number=42, sector_id=9101)
        gate = make_sector_stub(region_id=nexus_region.id, sector_number=1, sector_id=1)
        decoy_sector = make_sector_stub(region_id=region.id, sector_number=7, sector_id=9107)
        real_tunnel = make_tunnel(origin_sector_id=landing.id, destination_sector_id=gate.id)
        decoy_tunnel = make_tunnel(origin_sector_id=landing.id, destination_sector_id=decoy_sector.id)
        return region, nexus_region, landing, gate, decoy_sector, real_tunnel, decoy_tunnel

    def test_marker_present_for_personal_discovery(self):
        region, nexus_region, landing, gate, decoy_sector, tunnel, decoy_tunnel = self._scenario()
        player = make_player(current_region_id=region.id)
        knowledge = PlayerWarpKnowledge(
            player_id=player.id, warp_layer=WarpLayer.WARP_TUNNELS, warp_id=tunnel.id,
            visibility_state=WarpVisibilityState.REVEALED, revealed_via=WarpRevealedVia.SCAN,
        )
        db = _FakeQSession(
            regions=[region, nexus_region], sectors=[landing, gate, decoy_sector],
            tunnels=[tunnel, decoy_tunnel], knowledge=[knowledge],
        )

        marker = qs._resolve_nexus_warp_marker(db, player)

        assert marker == {"sector_id": landing.sector_id, "region_sector_number": 42}

    def test_marker_absent_when_only_corp_share_provenance(self):
        region, nexus_region, landing, gate, decoy_sector, tunnel, decoy_tunnel = self._scenario()
        player = make_player(current_region_id=region.id)
        knowledge = PlayerWarpKnowledge(
            player_id=player.id, warp_layer=WarpLayer.WARP_TUNNELS, warp_id=tunnel.id,
            visibility_state=WarpVisibilityState.REVEALED, revealed_via=WarpRevealedVia.CORP_SHARE,
        )
        db = _FakeQSession(
            regions=[region, nexus_region], sectors=[landing, gate, decoy_sector],
            tunnels=[tunnel, decoy_tunnel], knowledge=[knowledge],
        )

        assert qs._resolve_nexus_warp_marker(db, player) is None

    def test_marker_absent_when_nexus_warp_sector_is_null(self):
        region = make_region(nexus_warp_sector=None)
        player = make_player(current_region_id=region.id)
        db = _FakeQSession(regions=[region], sectors=[], tunnels=[], knowledge=[])

        assert qs._resolve_nexus_warp_marker(db, player) is None

    def test_marker_absent_when_not_personally_known_at_all(self):
        region, nexus_region, landing, gate, decoy_sector, tunnel, decoy_tunnel = self._scenario()
        player = make_player(current_region_id=region.id)
        db = _FakeQSession(
            regions=[region, nexus_region], sectors=[landing, gate, decoy_sector],
            tunnels=[tunnel, decoy_tunnel], knowledge=[],
        )

        assert qs._resolve_nexus_warp_marker(db, player) is None

    def test_disambiguates_from_decoy_intraregion_tunnel(self):
        """Knowing only the DECOY (ordinary intra-region NATURAL) tunnel must
        never unlock the Nexus marker — proves the Central-Nexus-endpoint
        disambiguation in ``_resolve_nexus_warp_marker`` actually gates on
        the right tunnel, not merely "any NATURAL tunnel touching the
        landing sector"."""
        region, nexus_region, landing, gate, decoy_sector, tunnel, decoy_tunnel = self._scenario()
        player = make_player(current_region_id=region.id)
        knowledge = PlayerWarpKnowledge(
            player_id=player.id, warp_layer=WarpLayer.WARP_TUNNELS, warp_id=decoy_tunnel.id,
            visibility_state=WarpVisibilityState.REVEALED, revealed_via=WarpRevealedVia.SCAN,
        )
        db = _FakeQSession(
            regions=[region, nexus_region], sectors=[landing, gate, decoy_sector],
            tunnels=[tunnel, decoy_tunnel], knowledge=[knowledge],
        )

        assert qs._resolve_nexus_warp_marker(db, player) is None


# --- Accept #6: no payment/subscription code touched ----------------------- #


class TestNoPaymentCodeTouched:
    """WO-GWQ-WARPSHARE Lane 2 HARD GATE: the nexus-marker tier read must be a
    plain field read, never touching payment/subscription service code — and
    if surfacing tier needed ANY payment code, that sub-part was to stay
    unbuilt. The shipped implementation needs NO tier read at all (see
    TestNexusMarker's docstring), so this is satisfied by construction.
    Pinned here so a future edit can't silently reintroduce one."""

    _QUANTUM_SERVICE_PATH = pathlib.Path(qs.__file__)
    _QUANTUM_ROUTE_PATH = _QUANTUM_SERVICE_PATH.parent.parent / "api" / "routes" / "quantum.py"

    _BANNED_SUBSTRINGS = (
        "paypal", "PayPal", "subscription_tier", "SubscriptionTier",
        "economy_faucet", "is_galactic_citizen",
    )

    def test_quantum_service_touches_no_payment_or_subscription_code(self):
        source = self._QUANTUM_SERVICE_PATH.read_text()
        offenders = [s for s in self._BANNED_SUBSTRINGS if s in source]
        assert offenders == []

    def test_quantum_route_touches_no_payment_or_subscription_code(self):
        source = self._QUANTUM_ROUTE_PATH.read_text()
        offenders = [s for s in self._BANNED_SUBSTRINGS if s in source]
        assert offenders == []
