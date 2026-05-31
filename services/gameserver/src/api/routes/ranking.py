"""
Military Ranking, Reputation & Bounty API Routes

Player-facing and admin endpoints for ranking, reputation, and bounty systems.
"""

import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from src.core.database import get_db
from src.auth.dependencies import get_current_player, get_current_admin
from src.models.player import Player
from src.models.user import User
from src.services.ranking_service import RankingService, RANK_DEFINITIONS
from src.services.bounty_service import BountyService
from src.services.personal_reputation_service import PersonalReputationService

router = APIRouter(
    prefix="/ranking",
    tags=["ranking"],
    responses={404: {"description": "Not found"}},
)


# ------------------------------------------------------------------
# Response models
# ------------------------------------------------------------------

class RankBonuses(BaseModel):
    trading_discount_percent: int
    max_turns_bonus: int
    combat_damage_bonus_percent: int


class RankInfoResponse(BaseModel):
    player_id: str
    username: str
    current_rank: str
    rank_level: int
    rank_tier: str = "Enlisted"
    rank_points: int
    points_to_next_rank: int
    next_rank: Optional[str] = None
    next_rank_points_required: Optional[int] = None
    progress_percent: float
    bonuses: RankBonuses
    is_max_rank: bool
    effective_max_turns: int = 1000
    aria_multiplier: float = 1.0


class RankDefinitionResponse(BaseModel):
    name: str
    points_required: int
    level: int
    tier: str = "Enlisted"
    trading_bonus: int = 0
    combat_bonus: int = 0
    max_turns_bonus: int = 0


class LeaderboardEntry(BaseModel):
    position: int
    player_id: str
    username: str
    military_rank: str
    rank_points: int
    rank_level: int


class LeaderboardResponse(BaseModel):
    entries: List[LeaderboardEntry]
    total_players: int


class PublicLeaderboardEntry(BaseModel):
    position: int
    player_id: str
    nickname: str
    military_rank: str
    score: int


class PublicLeaderboardResponse(BaseModel):
    category: str
    entries: List[PublicLeaderboardEntry]
    player_position: Optional[int] = None
    total_players: int


class RankRequirement(BaseModel):
    name: str
    current: int
    required: Optional[int] = None
    met: bool


class RankProgressResponse(BaseModel):
    player_id: str
    username: str
    current_rank: str
    rank_level: int
    rank_tier: str
    rank_points: int
    points_to_next_rank: int
    next_rank: Optional[str] = None
    next_rank_points_required: Optional[int] = None
    progress_percent: float
    bonuses: RankBonuses
    is_max_rank: bool
    effective_max_turns: int
    aria_multiplier: float
    stats: dict
    requirements: List[RankRequirement]


# ------------------------------------------------------------------
# Player endpoints
# ------------------------------------------------------------------

@router.get("/rank", response_model=RankInfoResponse)
async def get_player_rank(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Get the current player's rank information, progress, and bonuses."""
    ranking_service = RankingService(db)
    rank_info = ranking_service.get_rank_info(player.id)

    if not rank_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rank information not found",
        )

    return RankInfoResponse(**rank_info)


@router.get("/definitions", response_model=List[RankDefinitionResponse])
async def get_rank_definitions():
    """Get all rank definitions with their point thresholds."""
    return [RankDefinitionResponse(**rd) for rd in RANK_DEFINITIONS]


@router.get("/leaderboard/public", response_model=PublicLeaderboardResponse)
async def get_public_leaderboard(
    category: str = Query(
        default="rank_points",
        description="Leaderboard category: rank_points, combat, trading, exploration",
    ),
    limit: int = Query(default=20, ge=1, le=50),
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Get public leaderboard for various categories.

    Categories:
    - rank_points: Top players by military rank points
    - combat: Top players by combat victories
    - trading: Top players by total trade volume
    - exploration: Top players by ARIA interaction count (activity proxy)
    """
    from sqlalchemy import func as sa_func, desc as sa_desc, case

    valid_categories = {"rank_points", "combat", "trading", "exploration"}
    if category not in valid_categories:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid category '{category}'. Must be one of: {', '.join(sorted(valid_categories))}",
        )

    entries: List[PublicLeaderboardEntry] = []
    player_position: Optional[int] = None
    total_players = db.query(Player).filter(Player.is_active == True).count()

    if category == "rank_points":
        rows = (
            db.query(Player)
            .filter(Player.is_active == True)
            .order_by(sa_desc(Player.rank_points))
            .limit(limit)
            .all()
        )
        for pos, p in enumerate(rows, start=1):
            entries.append(PublicLeaderboardEntry(
                position=pos,
                player_id=str(p.id),
                nickname=p.username,
                military_rank=p.military_rank,
                score=p.rank_points or 0,
            ))

        # Find requesting player's position
        if not any(e.player_id == str(player.id) for e in entries):
            higher_count = (
                db.query(sa_func.count(Player.id))
                .filter(
                    Player.is_active == True,
                    Player.rank_points > (player.rank_points or 0),
                )
                .scalar()
            )
            player_position = (higher_count or 0) + 1
        else:
            player_position = next(
                e.position for e in entries if e.player_id == str(player.id)
            )

    elif category == "combat":
        from src.models.combat_log import CombatLog

        # Count victories: attacker wins + defender wins
        victory_subq = (
            db.query(
                sa_func.coalesce(
                    case(
                        (CombatLog.outcome == "attacker_win", CombatLog.attacker_id),
                        (CombatLog.outcome == "defender_win", CombatLog.defender_id),
                    ),
                ).label("winner_id"),
            )
            .filter(CombatLog.outcome.in_(["attacker_win", "defender_win"]))
            .subquery()
        )

        # Aggregate wins per player
        combat_rows = (
            db.query(
                victory_subq.c.winner_id,
                sa_func.count().label("wins"),
            )
            .group_by(victory_subq.c.winner_id)
            .order_by(sa_desc(sa_func.count()))
            .limit(limit)
            .all()
        )

        # Fetch player details for the winners
        winner_ids = [row.winner_id for row in combat_rows if row.winner_id is not None]
        players_map = {}
        if winner_ids:
            player_rows = (
                db.query(Player)
                .filter(Player.id.in_(winner_ids), Player.is_active == True)
                .all()
            )
            players_map = {p.id: p for p in player_rows}

        pos = 1
        for row in combat_rows:
            if row.winner_id is None:
                continue
            p = players_map.get(row.winner_id)
            if not p:
                continue
            entries.append(PublicLeaderboardEntry(
                position=pos,
                player_id=str(p.id),
                nickname=p.username,
                military_rank=p.military_rank,
                score=row.wins,
            ))
            pos += 1

        # Find requesting player's combat position
        if not any(e.player_id == str(player.id) for e in entries):
            # Count this player's victories
            player_wins_as_attacker = (
                db.query(sa_func.count(CombatLog.id))
                .filter(
                    CombatLog.attacker_id == player.id,
                    CombatLog.outcome == "attacker_win",
                )
                .scalar() or 0
            )
            player_wins_as_defender = (
                db.query(sa_func.count(CombatLog.id))
                .filter(
                    CombatLog.defender_id == player.id,
                    CombatLog.outcome == "defender_win",
                )
                .scalar() or 0
            )
            player_wins = player_wins_as_attacker + player_wins_as_defender

            # Count players with more wins (approximate position)
            higher_count = sum(1 for e in entries if e.score > player_wins)
            # If player not in top N, position is at least limit+1
            player_position = higher_count + 1 if player_wins > 0 else total_players
        else:
            player_position = next(
                e.position for e in entries if e.player_id == str(player.id)
            )

    elif category == "trading":
        from src.models.market_transaction import MarketTransaction

        trade_rows = (
            db.query(
                MarketTransaction.player_id,
                sa_func.sum(MarketTransaction.total_value).label("total_volume"),
            )
            .filter(MarketTransaction.player_id.isnot(None))
            .group_by(MarketTransaction.player_id)
            .order_by(sa_desc(sa_func.sum(MarketTransaction.total_value)))
            .limit(limit)
            .all()
        )

        trader_ids = [row.player_id for row in trade_rows if row.player_id is not None]
        players_map = {}
        if trader_ids:
            player_rows = (
                db.query(Player)
                .filter(Player.id.in_(trader_ids), Player.is_active == True)
                .all()
            )
            players_map = {p.id: p for p in player_rows}

        pos = 1
        for row in trade_rows:
            if row.player_id is None:
                continue
            p = players_map.get(row.player_id)
            if not p:
                continue
            entries.append(PublicLeaderboardEntry(
                position=pos,
                player_id=str(p.id),
                nickname=p.username,
                military_rank=p.military_rank,
                score=int(row.total_volume or 0),
            ))
            pos += 1

        # Find requesting player's trading position
        if not any(e.player_id == str(player.id) for e in entries):
            player_volume = (
                db.query(sa_func.sum(MarketTransaction.total_value))
                .filter(MarketTransaction.player_id == player.id)
                .scalar() or 0
            )
            higher_count = sum(1 for e in entries if e.score > int(player_volume))
            player_position = higher_count + 1 if player_volume > 0 else total_players
        else:
            player_position = next(
                e.position for e in entries if e.player_id == str(player.id)
            )

    elif category == "exploration":
        rows = (
            db.query(Player)
            .filter(Player.is_active == True)
            .order_by(sa_desc(Player.aria_total_interactions))
            .limit(limit)
            .all()
        )
        for pos, p in enumerate(rows, start=1):
            entries.append(PublicLeaderboardEntry(
                position=pos,
                player_id=str(p.id),
                nickname=p.username,
                military_rank=p.military_rank,
                score=p.aria_total_interactions or 0,
            ))

        # Find requesting player's position
        if not any(e.player_id == str(player.id) for e in entries):
            higher_count = (
                db.query(sa_func.count(Player.id))
                .filter(
                    Player.is_active == True,
                    Player.aria_total_interactions > (player.aria_total_interactions or 0),
                )
                .scalar()
            )
            player_position = (higher_count or 0) + 1
        else:
            player_position = next(
                e.position for e in entries if e.player_id == str(player.id)
            )

    return PublicLeaderboardResponse(
        category=category,
        entries=entries,
        player_position=player_position,
        total_players=total_players,
    )


@router.get("/progress", response_model=RankProgressResponse)
async def get_rank_progress(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Get detailed progress toward next rank including achievement stats and requirements.

    Returns current rank info combined with the player's combat, trading, and
    exploration statistics, plus which rank-progression requirements are met.
    """
    from sqlalchemy import func as sa_func
    from src.models.combat_log import CombatLog
    from src.models.market_transaction import MarketTransaction

    ranking_service = RankingService(db)
    rank_info = ranking_service.get_rank_info(player.id)

    if not rank_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rank information not found",
        )

    # Gather player achievement stats
    combat_victories_as_attacker = (
        db.query(sa_func.count(CombatLog.id))
        .filter(
            CombatLog.attacker_id == player.id,
            CombatLog.outcome == "attacker_win",
        )
        .scalar() or 0
    )
    combat_victories_as_defender = (
        db.query(sa_func.count(CombatLog.id))
        .filter(
            CombatLog.defender_id == player.id,
            CombatLog.outcome == "defender_win",
        )
        .scalar() or 0
    )
    combat_victories = combat_victories_as_attacker + combat_victories_as_defender

    total_trades = (
        db.query(sa_func.count(MarketTransaction.id))
        .filter(MarketTransaction.player_id == player.id)
        .scalar() or 0
    )
    trade_volume = (
        db.query(sa_func.sum(MarketTransaction.total_value))
        .filter(MarketTransaction.player_id == player.id)
        .scalar() or 0
    )

    sectors_visited = player.aria_total_interactions or 0  # proxy for exploration

    stats = {
        "combat_victories": combat_victories,
        "total_trades": total_trades,
        "trade_volume": int(trade_volume),
        "exploration_score": sectors_visited,
        "credits": player.credits or 0,
        "turns_remaining": player.turns or 0,
    }

    # Build requirements checklist for the next rank
    requirements: List[RankRequirement] = []
    next_rank_points = rank_info.get("next_rank_points_required")

    requirements.append(RankRequirement(
        name="Rank Points",
        current=rank_info["rank_points"],
        required=next_rank_points,
        met=rank_info["is_max_rank"] or (rank_info["rank_points"] >= (next_rank_points or 0)),
    ))
    requirements.append(RankRequirement(
        name="Combat Victories",
        current=combat_victories,
        required=None,  # No hard requirement, but tracked for progress
        met=combat_victories > 0,
    ))
    requirements.append(RankRequirement(
        name="Trade Volume",
        current=int(trade_volume),
        required=None,
        met=int(trade_volume) > 0,
    ))
    requirements.append(RankRequirement(
        name="Exploration Activity",
        current=sectors_visited,
        required=None,
        met=sectors_visited > 0,
    ))

    return RankProgressResponse(
        player_id=rank_info["player_id"],
        username=rank_info["username"],
        current_rank=rank_info["current_rank"],
        rank_level=rank_info["rank_level"],
        rank_tier=rank_info["rank_tier"],
        rank_points=rank_info["rank_points"],
        points_to_next_rank=rank_info["points_to_next_rank"],
        next_rank=rank_info["next_rank"],
        next_rank_points_required=rank_info["next_rank_points_required"],
        progress_percent=rank_info["progress_percent"],
        bonuses=RankBonuses(**rank_info["bonuses"]),
        is_max_rank=rank_info["is_max_rank"],
        effective_max_turns=rank_info["effective_max_turns"],
        aria_multiplier=rank_info["aria_multiplier"],
        stats=stats,
        requirements=requirements,
    )


# ------------------------------------------------------------------
# Admin endpoints
# ------------------------------------------------------------------

@router.get("/leaderboard", response_model=LeaderboardResponse)
async def get_rankings_leaderboard(
    limit: int = Query(default=20, ge=1, le=100, description="Number of players to return"),
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Get the top players ranked by military rank points. Admin only."""
    ranking_service = RankingService(db)
    entries = ranking_service.get_leaderboard(limit=limit)

    # Count total active players for context
    total_players = db.query(Player).filter(Player.is_active == True).count()

    return LeaderboardResponse(
        entries=[LeaderboardEntry(**e) for e in entries],
        total_players=total_players,
    )


# ------------------------------------------------------------------
# Medal endpoints
# ------------------------------------------------------------------

@router.get("/medals")
async def get_player_medals(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Get the current player's earned and available medals."""
    from src.services.medal_service import MedalService
    medal_service = MedalService(db)
    result = medal_service.get_player_medals(player.id)
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message", "Failed to get medals"),
        )
    # Project to a known-safe subset so any exception detail / stack-trace the
    # service may have stuffed into the dict can't reach the client
    # (py/stack-trace-exposure).
    return {
        "success": True,
        "earned": result.get("earned", []),
        "available": result.get("available", []),
        "stats": result.get("stats", {}),
    }


# ------------------------------------------------------------------
# Reputation endpoints
# ------------------------------------------------------------------

@router.get("/reputation")
async def get_player_reputation(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Get the current player's personal reputation info."""
    rep_service = PersonalReputationService(db)
    result = rep_service.get_reputation_info(player.id)
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result.get("message", "Reputation info not found"),
        )
    return result


# ------------------------------------------------------------------
# Bounty endpoints
# ------------------------------------------------------------------

class PlaceBountyRequest(BaseModel):
    target_id: str
    amount: int = Field(..., gt=0, le=1000000, description="Bounty amount (1 to 1,000,000 credits)")


@router.post("/bounties/place")
async def place_bounty(
    request: PlaceBountyRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Place a bounty on another player. Costs amount + 10% fee."""
    bounty_service = BountyService(db)
    result = bounty_service.place_bounty(
        player.id, uuid.UUID(request.target_id), request.amount
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message", "Failed to place bounty"),
        )
    db.commit()
    return result


@router.get("/bounties/target/{player_id}")
async def get_bounties_on_player(
    player_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Get all bounties on a specific player."""
    bounty_service = BountyService(db)
    result = bounty_service.get_bounties_on_player(uuid.UUID(player_id))
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result.get("message", "Player not found"),
        )
    return result


@router.get("/bounties/available")
async def get_available_bounties(
    limit: int = Query(default=20, ge=1, le=100),
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """List all players with active bounties."""
    bounty_service = BountyService(db)
    return bounty_service.get_available_bounties(limit=limit)
