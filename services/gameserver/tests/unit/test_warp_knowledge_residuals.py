"""WO-ARIA-WARP-RESIDUALS — the two remaining unbuilt ``WarpRevealedVia``
reveal paths (aria-companion.md § Warp discovery, :63-76): ``TRAVERSAL_ATTEMPT``
and the deliberate bulk ``CORP_SHARE`` action.

PREMISE VERDICT (resolved before building, verify-first): a repo-wide grep
confirmed ``CORP_SHARE`` already has an AUTOMATIC writer
(``movement_service._propagate_warp_reveal_to_team``, WO-GWQ-WARPSHARE,
already tested by ``test_warp_corp_share.py``) that fans a reveal out to
current teammates every time ANY warp is revealed to a player. That is a
DIFFERENT mechanism from this WO's Lane B: a DELIBERATE, one-time BULK share
of everything the caller already knows (``movement_service.
share_warp_knowledge_with_team``), for knowledge the automatic per-reveal
fan-out never retroactively backfills (pre-existing discoveries, or a
teammate who joined after the fact). ``TRAVERSAL_ATTEMPT`` had ZERO writers
anywhere (confirmed) before this WO's Lane A
(``movement_service._handle_reverse_one_way_traversal_attempt``, called from
``_check_warp_tunnel`` right before it gives up).

DB-free: hand-built fakes, no real DB/app — mirrors test_warp_corp_share.py's
own established convention (``PlayerWarpKnowledge`` rows are real, unattached
ORM instances; fake queries walk the REAL SQLAlchemy BinaryExpression/
BooleanClauseList objects via ``.left.key``/``.right.value``/``.operator``).
Not shared via import with that sibling file (this codebase's "each test file
keeps its own self-contained harness" convention) — the matcher/fake-session
shapes are re-derived here, extended for the additional Sector/WarpTunnel
query surface ``_check_warp_tunnel``/``_handle_reverse_one_way_traversal_
attempt`` need that the sibling file never exercised.
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
from src.models.sector import Sector
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus, WarpTunnelType
from src.services import movement_service as ms


# ---------------------------------------------------------------------------
# Shared fixture builders + matcher (mirrors test_warp_corp_share.py's own
# conventions, extended for Sector/WarpTunnel)
# ---------------------------------------------------------------------------

def make_player(*, team_id: Any = None) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4(), team_id=team_id)


def make_team_member(team_id: Any, player_id: Any) -> SimpleNamespace:
    return SimpleNamespace(team_id=team_id, player_id=player_id)


def make_sector(*, sector_id: int) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), sector_id=sector_id)


def make_tunnel(
    *, origin_sector_id, destination_sector_id,
    is_bidirectional: bool, is_latent: bool,
    status: WarpTunnelStatus = WarpTunnelStatus.ACTIVE,
    type_: WarpTunnelType = WarpTunnelType.NATURAL,
    created_by_player_id: Any = None,
    turn_cost: int = 2,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), origin_sector_id=origin_sector_id, destination_sector_id=destination_sector_id,
        is_bidirectional=is_bidirectional, is_latent=is_latent, status=status, type=type_,
        created_by_player_id=created_by_player_id, turn_cost=turn_cost, properties={},
    )


def make_ship(*, owner_id: Any) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), owner_id=owner_id, warp_capable=False)


def _right_value(right: Any) -> Any:
    """Extract the Python value a BinaryExpression's right side represents.
    A bound comparison (``Model.col == some_value``) exposes ``.value``
    directly. A LITERAL boolean comparison (``Model.col == False`` /
    ``== True``) compiles to SQLAlchemy's ``sqlalchemy.sql.elements.False_``/
    ``True_`` SINGLETON instead of a bound parameter -- it has no ``.value``
    attribute at all (``getattr(right, "value", None)`` silently returns
    ``None``, which would misread ``== False`` as ``== None`` and desync
    the whole match), and its own ``__bool__`` raises TypeError rather than
    resolving to a plain bool. Verified against a live SQLAlchemy probe at
    authoring time (WO-ARIA-WARP-RESIDUALS) before relying on this.
    """
    from sqlalchemy.sql.elements import False_, True_

    if isinstance(right, False_):
        return False
    if isinstance(right, True_):
        return True
    return getattr(right, "value", None)


def _cond_kv(cond: Any):
    left = getattr(cond, "left", None)
    right = getattr(cond, "right", None)
    return getattr(left, "key", None), _right_value(right), getattr(cond, "operator", _op.eq)


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
    opname = getattr(op, "__name__", None)
    if opname == "ne":
        return actual != val
    if opname == "in_op":
        return actual in val
    return actual == val


class _FakeGenericQuery:
    """db.query(Model).filter(...).first()/.all() -- a plain conjunction
    matcher (handles or_() groups too via ``_match``), for Sector /
    WarpTunnel / PlayerWarpKnowledge."""

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


class _FakeScalarTeamIdQuery:
    """db.query(Player.team_id).filter(Player.id == x).scalar()."""

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
    """db.query(Player.id, Player.user_id).join(TeamMember, ...).filter(
    TeamMember.team_id == x, Player.id != y).all() -- _propagate_warp_
    reveal_to_team's two-column shape."""

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


class _FakeTeammateIdOnlyQuery:
    """db.query(Player.id).join(TeamMember, ...).filter(TeamMember.team_id
    == x, Player.id != y).all() -- share_warp_knowledge_with_team's
    single-column shape (distinct from the two-column join above)."""

    def __init__(self, players: List[SimpleNamespace], team_members: List[SimpleNamespace]) -> None:
        self._players = players
        self._team_members = team_members
        self._team_id = None
        self._exclude_id = None

    def join(self, *a: Any, **k: Any) -> "_FakeTeammateIdOnlyQuery":
        return self

    def filter(self, *conds: Any) -> "_FakeTeammateIdOnlyQuery":
        for cond in conds:
            key, val, op = _cond_kv(cond)
            if key == "team_id" and op is _op.eq:
                self._team_id = val
            elif key == "id" and op is _op.ne:
                self._exclude_id = val
        return self

    def all(self):
        member_ids = {tm.player_id for tm in self._team_members if tm.team_id == self._team_id}
        return [(p.id,) for p in self._players if p.id in member_ids and p.id != self._exclude_id]


class _FakeMovementSession:
    """In-memory Session double covering every query shape
    ``_check_warp_tunnel`` / ``_handle_reverse_one_way_traversal_attempt`` /
    ``_reveal_warp_to_player`` / ``_propagate_warp_reveal_to_team`` /
    ``share_warp_knowledge_with_team`` issue. ``knowledge`` is the durable
    "committed" PlayerWarpKnowledge store; ``add()`` stages, ``flush()``
    (also run implicitly by ``commit()``) is where a UNIQUE violation
    surfaces -- mirrors test_warp_corp_share.py's ``_FakeMoveSession``
    exactly for that part.
    """

    def __init__(
        self, *, sectors=(), tunnels=(), knowledge_rows=(), players=(), team_members=(),
    ) -> None:
        self.sectors = list(sectors)
        self.tunnels = list(tunnels)
        self.knowledge: List[PlayerWarpKnowledge] = list(knowledge_rows)
        self.players = list(players)
        self.team_members = list(team_members)
        self._pending: List[Any] = []
        self.flush_count = 0
        self.committed = False
        self.rolled_back = False

    def query(self, *args: Any) -> Any:
        if len(args) == 1 and args[0] is Sector:
            return _FakeGenericQuery(self.sectors)
        if len(args) == 1 and args[0] is WarpTunnel:
            return _FakeGenericQuery(self.tunnels)
        if len(args) == 1 and args[0] is PlayerWarpKnowledge:
            return _FakeGenericQuery(self.knowledge)
        if len(args) == 1 and getattr(args[0], "key", None) == "team_id":
            return _FakeScalarTeamIdQuery(self.players)
        if len(args) == 1 and getattr(args[0], "key", None) == "id":
            return _FakeTeammateIdOnlyQuery(self.players, self.team_members)
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
                dup = any(
                    r.player_id == obj.player_id
                    and r.warp_layer == obj.warp_layer
                    and r.warp_id == obj.warp_id
                    for r in self.knowledge
                )
                if dup:
                    self._pending = []
                    raise IntegrityError("INSERT", {}, Exception("duplicate key value violates unique constraint"))
                self.knowledge.append(obj)
            else:
                still_pending.append(obj)
        self._pending = still_pending

    @contextmanager
    def begin_nested(self):
        yield

    def commit(self) -> None:
        self.flush()
        self.committed = True

    def rollback(self) -> None:
        self._pending = []
        self.rolled_back = True


# ---------------------------------------------------------------------------
# Lane A -- reverse traversal of a latent one-way tunnel
# ---------------------------------------------------------------------------

class TestReverseOneWayTraversalAttempt:
    def _scenario(self, *, is_latent=True, is_bidirectional=False):
        origin = make_sector(sector_id=100)   # "current" -- where the player IS
        far = make_sector(sector_id=200)      # "destination" the player wants to reach
        # The REAL tunnel only runs far -> origin (one-way, latent) -- the
        # player is standing at origin and trying to go origin -> far,
        # which is the WRONG direction on this one-way warp.
        tunnel = make_tunnel(
            origin_sector_id=far.id, destination_sector_id=origin.id,
            is_bidirectional=is_bidirectional, is_latent=is_latent,
        )
        return origin, far, tunnel

    def test_reverse_attempt_reveals_and_move_still_fails(self):
        origin, far, tunnel = self._scenario()
        player = make_player()
        ship = make_ship(owner_id=player.id)
        db = _FakeMovementSession(sectors=[origin, far], tunnels=[tunnel], players=[player])
        service = ms.MovementService(db)

        can_move, cost, message = service._check_warp_tunnel(origin.sector_id, far.sector_id, ship)

        # The move fails EXACTLY as before -- same shape, same message.
        assert can_move is False
        assert cost == 0
        assert message == "No active warp tunnel found"

        assert len(db.knowledge) == 1
        row = db.knowledge[0]
        assert row.player_id == player.id
        assert row.warp_layer == WarpLayer.WARP_TUNNELS
        assert row.warp_id == tunnel.id
        assert row.revealed_via == WarpRevealedVia.TRAVERSAL_ATTEMPT
        assert row.visibility_state == WarpVisibilityState.REVEALED
        assert db.committed is True

    def test_no_connection_at_all_still_fails_identically_and_writes_nothing(self):
        """Regression pin: when there is truly no tunnel in either
        direction, the failure message and (lack of) side effects must be
        byte-identical to before this WO."""
        origin = make_sector(sector_id=100)
        far = make_sector(sector_id=200)
        player = make_player()
        ship = make_ship(owner_id=player.id)
        db = _FakeMovementSession(sectors=[origin, far], tunnels=[], players=[player])
        service = ms.MovementService(db)

        can_move, cost, message = service._check_warp_tunnel(origin.sector_id, far.sector_id, ship)

        assert can_move is False
        assert cost == 0
        assert message == "No active warp tunnel found"
        assert db.knowledge == []
        assert db.committed is False

    def test_non_latent_reverse_tunnel_writes_no_row(self):
        """A non-latent one-way tunnel traversed backwards is still just a
        normal failed move -- nothing to reveal, ARIA already knows the
        player can see it (or it was never hidden to begin with)."""
        origin, far, tunnel = self._scenario(is_latent=False)
        player = make_player()
        ship = make_ship(owner_id=player.id)
        db = _FakeMovementSession(sectors=[origin, far], tunnels=[tunnel], players=[player])
        service = ms.MovementService(db)

        can_move, cost, message = service._check_warp_tunnel(origin.sector_id, far.sector_id, ship)

        assert can_move is False
        assert db.knowledge == []
        assert db.committed is False

    def test_genuinely_bidirectional_reverse_tunnel_succeeds_normally(self):
        """Sanity: a REAL bidirectional tunnel traversed in reverse takes
        the EXISTING success path (matched by the pre-existing reverse-
        bidirectional query) and never reaches the new one-way-attempt
        code at all -- no reveal side effect fires for an ordinary,
        already-supported reverse traversal."""
        origin, far, tunnel = self._scenario(is_latent=False, is_bidirectional=True)
        player = make_player()
        ship = make_ship(owner_id=player.id)
        db = _FakeMovementSession(sectors=[origin, far], tunnels=[tunnel], players=[player])
        service = ms.MovementService(db)

        can_move, cost, message = service._check_warp_tunnel(origin.sector_id, far.sector_id, ship)

        assert can_move is True
        assert db.knowledge == []  # no TRAVERSAL_ATTEMPT reveal -- this path never fires

    def test_idempotent_repeated_attempts_write_one_row(self):
        origin, far, tunnel = self._scenario()
        player = make_player()
        ship = make_ship(owner_id=player.id)
        db = _FakeMovementSession(sectors=[origin, far], tunnels=[tunnel], players=[player])
        service = ms.MovementService(db)

        service._check_warp_tunnel(origin.sector_id, far.sector_id, ship)
        service._check_warp_tunnel(origin.sector_id, far.sector_id, ship)
        service._check_warp_tunnel(origin.sector_id, far.sector_id, ship)

        assert len(db.knowledge) == 1
        assert db.knowledge[0].revealed_via == WarpRevealedVia.TRAVERSAL_ATTEMPT

    def test_never_downgrades_an_already_traversed_row(self):
        origin, far, tunnel = self._scenario()
        player = make_player()
        ship = make_ship(owner_id=player.id)
        traversed_row = PlayerWarpKnowledge(
            player_id=player.id, warp_layer=WarpLayer.WARP_TUNNELS, warp_id=tunnel.id,
            visibility_state=WarpVisibilityState.TRAVERSED, revealed_via=WarpRevealedVia.SCAN,
        )
        db = _FakeMovementSession(
            sectors=[origin, far], tunnels=[tunnel], players=[player], knowledge_rows=[traversed_row],
        )
        service = ms.MovementService(db)

        service._check_warp_tunnel(origin.sector_id, far.sector_id, ship)

        assert len(db.knowledge) == 1
        row = db.knowledge[0]
        assert row.visibility_state == WarpVisibilityState.TRAVERSED  # never downgraded
        assert row.revealed_via == WarpRevealedVia.SCAN  # provenance untouched


# ---------------------------------------------------------------------------
# Lane B -- deliberate bulk share to current team members
# ---------------------------------------------------------------------------

class TestShareWarpKnowledgeWithTeam:
    def test_propagates_to_current_members_only_skips_ex_members(self):
        team_id = uuid.uuid4()
        sharer = make_player(team_id=team_id)
        member_b = make_player(team_id=team_id)
        member_c = make_player(team_id=team_id)
        ex_member = make_player(team_id=None)  # left the team -- no TeamMember row

        team_members = [
            make_team_member(team_id, sharer.id),
            make_team_member(team_id, member_b.id),
            make_team_member(team_id, member_c.id),
        ]
        tunnel_1, tunnel_2 = uuid.uuid4(), uuid.uuid4()
        sharer_rows = [
            PlayerWarpKnowledge(
                player_id=sharer.id, warp_layer=WarpLayer.WARP_TUNNELS, warp_id=tunnel_1,
                visibility_state=WarpVisibilityState.REVEALED, revealed_via=WarpRevealedVia.SCAN,
            ),
            PlayerWarpKnowledge(
                player_id=sharer.id, warp_layer=WarpLayer.WARP_TUNNELS, warp_id=tunnel_2,
                visibility_state=WarpVisibilityState.TRAVERSED, revealed_via=WarpRevealedVia.SCAN,
            ),
        ]
        db = _FakeMovementSession(
            players=[sharer, member_b, member_c, ex_member], team_members=team_members,
            knowledge_rows=list(sharer_rows),
        )

        result = ms.share_warp_knowledge_with_team(db, sharer.id, team_id)

        assert result == {"shared_warp_count": 2, "recipient_count": 2, "rows_created": 4}
        b_rows = [r for r in db.knowledge if r.player_id == member_b.id]
        c_rows = [r for r in db.knowledge if r.player_id == member_c.id]
        assert len(b_rows) == 2 and len(c_rows) == 2
        assert all(r.revealed_via == WarpRevealedVia.CORP_SHARE for r in b_rows + c_rows)
        assert all(r.visibility_state == WarpVisibilityState.REVEALED for r in b_rows + c_rows)
        # The ex-member (not a CURRENT TeamMember row) gets nothing at all.
        assert all(r.player_id != ex_member.id for r in db.knowledge)

    def test_never_overwrites_a_members_existing_row(self):
        team_id = uuid.uuid4()
        sharer = make_player(team_id=team_id)
        member_b = make_player(team_id=team_id)
        team_members = [make_team_member(team_id, sharer.id), make_team_member(team_id, member_b.id)]
        tunnel_id = uuid.uuid4()

        sharer_row = PlayerWarpKnowledge(
            player_id=sharer.id, warp_layer=WarpLayer.WARP_TUNNELS, warp_id=tunnel_id,
            visibility_state=WarpVisibilityState.REVEALED, revealed_via=WarpRevealedVia.SCAN,
        )
        # B already personally TRAVERSED this same warp -- a share must
        # never touch it.
        b_traversed = PlayerWarpKnowledge(
            player_id=member_b.id, warp_layer=WarpLayer.WARP_TUNNELS, warp_id=tunnel_id,
            visibility_state=WarpVisibilityState.TRAVERSED, revealed_via=WarpRevealedVia.SCAN,
        )
        db = _FakeMovementSession(
            players=[sharer, member_b], team_members=team_members,
            knowledge_rows=[sharer_row, b_traversed],
        )

        result = ms.share_warp_knowledge_with_team(db, sharer.id, team_id)

        assert result["rows_created"] == 0
        b_row = next(r for r in db.knowledge if r.player_id == member_b.id)
        assert b_row.visibility_state == WarpVisibilityState.TRAVERSED
        assert b_row.revealed_via == WarpRevealedVia.SCAN
        assert len([r for r in db.knowledge if r.player_id == member_b.id]) == 1  # no duplicate

    def test_post_share_joiner_gets_zero_rows(self):
        """A player who joins the team AFTER the share call has zero rows
        from it -- no ongoing sync (aria-companion.md:65)."""
        team_id = uuid.uuid4()
        sharer = make_player(team_id=team_id)
        team_members = [make_team_member(team_id, sharer.id)]  # solo team at share time
        tunnel_id = uuid.uuid4()
        sharer_row = PlayerWarpKnowledge(
            player_id=sharer.id, warp_layer=WarpLayer.WARP_TUNNELS, warp_id=tunnel_id,
            visibility_state=WarpVisibilityState.REVEALED, revealed_via=WarpRevealedVia.SCAN,
        )
        db = _FakeMovementSession(players=[sharer], team_members=team_members, knowledge_rows=[sharer_row])

        result = ms.share_warp_knowledge_with_team(db, sharer.id, team_id)
        assert result["recipient_count"] == 0

        # A new player joins AFTER the share -- simulated by adding them to
        # the roster now and confirming a re-query finds no rows for them
        # (the share already ran and is not re-triggered by membership
        # changes).
        late_joiner = make_player(team_id=team_id)
        db.players.append(late_joiner)
        db.team_members.append(make_team_member(team_id, late_joiner.id))

        assert all(r.player_id != late_joiner.id for r in db.knowledge)

    def test_idempotent_second_share_creates_no_duplicates(self):
        team_id = uuid.uuid4()
        sharer = make_player(team_id=team_id)
        member_b = make_player(team_id=team_id)
        team_members = [make_team_member(team_id, sharer.id), make_team_member(team_id, member_b.id)]
        tunnel_id = uuid.uuid4()
        sharer_row = PlayerWarpKnowledge(
            player_id=sharer.id, warp_layer=WarpLayer.WARP_TUNNELS, warp_id=tunnel_id,
            visibility_state=WarpVisibilityState.REVEALED, revealed_via=WarpRevealedVia.SCAN,
        )
        db = _FakeMovementSession(players=[sharer, member_b], team_members=team_members, knowledge_rows=[sharer_row])

        first = ms.share_warp_knowledge_with_team(db, sharer.id, team_id)
        second = ms.share_warp_knowledge_with_team(db, sharer.id, team_id)

        assert first["rows_created"] == 1
        assert second["rows_created"] == 0
        assert len([r for r in db.knowledge if r.player_id == member_b.id]) == 1

    def test_solo_sharer_with_no_teammates_shares_to_nobody(self):
        team_id = uuid.uuid4()
        sharer = make_player(team_id=team_id)
        team_members = [make_team_member(team_id, sharer.id)]
        sharer_row = PlayerWarpKnowledge(
            player_id=sharer.id, warp_layer=WarpLayer.WARP_TUNNELS, warp_id=uuid.uuid4(),
            visibility_state=WarpVisibilityState.REVEALED, revealed_via=WarpRevealedVia.SCAN,
        )
        db = _FakeMovementSession(players=[sharer], team_members=team_members, knowledge_rows=[sharer_row])

        result = ms.share_warp_knowledge_with_team(db, sharer.id, team_id)

        assert result == {"shared_warp_count": 1, "recipient_count": 0, "rows_created": 0}

    def test_sharer_with_no_known_warps_is_a_clean_no_op(self):
        team_id = uuid.uuid4()
        sharer = make_player(team_id=team_id)
        member_b = make_player(team_id=team_id)
        team_members = [make_team_member(team_id, sharer.id), make_team_member(team_id, member_b.id)]
        db = _FakeMovementSession(players=[sharer, member_b], team_members=team_members, knowledge_rows=[])

        result = ms.share_warp_knowledge_with_team(db, sharer.id, team_id)

        assert result == {"shared_warp_count": 0, "recipient_count": 0, "rows_created": 0}


# ---------------------------------------------------------------------------
# Acceptance: grep corp_share|traversal_attempt in src/services + src/api
# goes 0 -> >0 with LIVE writers (source-scoped pin, mirrors test_warp_
# corp_share.py's TestCorpShareHasWriters).
# ---------------------------------------------------------------------------

class TestBothPathsHaveWriters:
    _MOVEMENT_SERVICE_PATH = pathlib.Path(ms.__file__)
    _TEAMS_ROUTE_PATH = _MOVEMENT_SERVICE_PATH.parent.parent / "api" / "routes" / "teams.py"

    def test_traversal_attempt_is_written_in_movement_service(self):
        source = self._MOVEMENT_SERVICE_PATH.read_text()
        assert "revealed_via=WarpRevealedVia.TRAVERSAL_ATTEMPT" in source
        assert "_handle_reverse_one_way_traversal_attempt" in source

    def test_share_warp_knowledge_is_written_in_movement_service_and_routed_in_teams_api(self):
        movement_source = self._MOVEMENT_SERVICE_PATH.read_text()
        teams_source = self._TEAMS_ROUTE_PATH.read_text()

        assert "def share_warp_knowledge_with_team" in movement_source
        assert "revealed_via=WarpRevealedVia.CORP_SHARE" in movement_source
        assert "share_warp_knowledge_with_team" in teams_source
        assert "share-warp-knowledge" in teams_source
