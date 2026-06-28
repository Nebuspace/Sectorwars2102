"""
Retention — per-player at-risk signal computation (WO-RE2).

Canon: ``OPERATIONS/retention.md`` "At-risk signals". Seven signals are computed
**per player**, each a threshold check on that player's OWN login / session /
combat / economic / social history from the durable analytics tables
(``PlayerActivity``, ``PlayerSession``) — no cross-player aggregation, no ML
clustering (per ADR-0016 "per-player ARIA, no aggregate ML").

| Signal                     | Canon threshold                                        |
|----------------------------|--------------------------------------------------------|
| dormant_session            | No login in 7+ days                                    |
| lapsed                     | No login in 30+ days                                   |
| declining_session_length   | Last 5 sessions trending down (>30% drop)             |
| early_logout_streak        | 3 consecutive sessions < 5 minutes                    |
| negative_combat_streak     | Recent kill/death ratio inverted                      |
| economic_loss_streak       | Recent net credit loss > 50% of holdings              |
| social_isolation           | Solo player with no team / messages in 14 days        |

READ-ONLY GUARANTEE: this module NEVER mutates PlayerActivity, PlayerSession,
CombatLog, Message, Team, or Player. It only issues SELECTs against them. The
ONLY write in the retention pipeline is the re-engagement-queue upsert, which
lives in the scheduler sweep (``npc_scheduler_service._run_retention_sweep_async``)
— never here. ``compute_player_signals`` is a pure read → verdict function.

Clock domain: windows are measured in CANONICAL days/hours
(``game_time.canonical_hours_since``), matching the rest of the scheduler's
day-gated machinery (``_canonical_days_inactive`` in npc_scheduler_service) so
the signals are observable on dev (GAME_TIME_SCALE=144 → a canonical day elapses
in ~10 wall-clock minutes) and consistent with inactivity-decay. The canon
states the windows in "days" without pinning the clock domain; canonical days is
the project-consistent reading. (Where a threshold's exact comparator/lookback is
not literally pinned by canon, the choice is annotated NO-CANON below.)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.core import game_time

logger = logging.getLogger(__name__)

UTC = timezone.utc

# ============================================================================
# SIGNAL DATA-SOURCE STATUS (reviewer HIGH — read before trusting a signal).
# The 7 canonical signals split by whether their data source is populated TODAY:
#   LIVE (real Postgres data, fire correctly):
#     - dormant_session / lapsed   ← Player.last_game_login (the ONE durable
#       analytics write-back per OPERATIONS/player-activity.md)
#     - negative_combat_streak     ← CombatLog (written by combat_service)
#     - social_isolation           ← Player.team_id + Message
#   DORMANT (logic correct, but their SQL tables are NEVER written — the
#   PlayerActivity / PlayerSession telemetry is REDIS-ONLY per
#   OPERATIONS/player-activity.md "no write-back into Postgres"; these SELECTs
#   read empty tables and return None, so the signals never fire until a durable
#   analytics write-back lands):
#     - declining_session_length / early_logout_streak  ← PlayerSession (empty)
#     - economic_loss_streak                            ← PlayerActivity (empty)
#   This is flagged for DECISIONS.md. The same empty-PlayerActivity caveat
#   affects WO-G18's Region.active_players_30d recompute (separate follow-up).
# ============================================================================

# --- Canonical thresholds (OPERATIONS/retention.md "At-risk signals") --------
DORMANT_DAYS = 7                 # canon: "No login in 7+ days"
LAPSED_DAYS = 30                 # canon: "No login in 30+ days"
DECLINING_DROP_PCT = 0.30        # canon: ">30% drop" over the last 5 sessions
DECLINING_MIN_SESSIONS = 5       # canon: "Last 5 sessions"
EARLY_LOGOUT_MINUTES = 5         # canon: "sessions < 5 minutes"
EARLY_LOGOUT_STREAK = 3          # canon: "3 consecutive sessions"
ECONOMIC_LOSS_PCT = 0.50         # canon: "net credit loss > 50% of holdings"
SOCIAL_ISOLATION_DAYS = 14       # canon: "no team / messages in 14 days"

# --- NO-CANON lookback/comparator choices (canon names the streak but not the
#     exact lookback window or kill/death tie-break — smallest reasonable choice,
#     flagged so the Orchestrator can pin them in DECISIONS.md if desired) -----
COMBAT_LOOKBACK_DAYS = 14        # NO-CANON: window for "recent" kill/death ratio
COMBAT_MIN_EVENTS = 3            # NO-CANON: min combats before the ratio is meaningful
ECONOMIC_LOOKBACK_DAYS = 14      # NO-CANON: window for "recent" net credit delta

# Canonical signal labels (order = report order).
SIGNAL_LABELS = (
    "dormant_session",
    "lapsed",
    "declining_session_length",
    "early_logout_streak",
    "negative_combat_streak",
    "economic_loss_streak",
    "social_isolation",
)


class RetentionService:
    """Read-only at-risk signal computer. One instance wraps a sync Session and
    is reused across the nightly sweep's per-player loop."""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def compute_player_signals(
        self, player_id: Any, now: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Compute all 7 at-risk signals for one player. READ-ONLY.

        Returns ``{"tripped": [labels...], "detail": {label: {...evidence...}}}``.
        Each tripped label carries the threshold it used + the observed value so
        the flag is auditable and the campaign layer can target it. A player with
        no tripped signals returns an empty ``tripped`` list (not flagged).

        Never raises for "no data" — a player with no sessions / no combat /
        no trades simply trips no signals on those dimensions. Genuine query
        errors propagate to the caller's per-player isolation.
        """
        now = now or datetime.now(UTC)
        tripped: List[str] = []
        detail: Dict[str, Dict[str, Any]] = {}

        from src.models.player import Player

        player = self.db.query(Player).filter(Player.id == player_id).first()
        if player is None:
            return {"tripped": [], "detail": {}}

        # --- 1 & 2: dormant_session / lapsed (login recency) --------------
        days_inactive = self._canonical_days_inactive(player, now)
        if days_inactive is not None:
            if days_inactive >= LAPSED_DAYS:
                tripped.append("lapsed")
                detail["lapsed"] = {
                    "threshold_days": LAPSED_DAYS,
                    "observed_days": days_inactive,
                }
            elif days_inactive >= DORMANT_DAYS:
                # lapsed SUPERSEDES dormant (a lapsed player is also dormant; we
                # report the more severe single login-recency signal — NO-CANON
                # mutual-exclusion choice, smallest-intervention).
                tripped.append("dormant_session")
                detail["dormant_session"] = {
                    "threshold_days": DORMANT_DAYS,
                    "observed_days": days_inactive,
                }

        # --- 3 & 4: session-length signals (need completed sessions) ------
        durations = self._recent_session_durations(player_id)
        decl = self._declining_session_length(durations)
        if decl is not None:
            tripped.append("declining_session_length")
            detail["declining_session_length"] = decl
        early = self._early_logout_streak(durations)
        if early is not None:
            tripped.append("early_logout_streak")
            detail["early_logout_streak"] = early

        # --- 5: negative_combat_streak -----------------------------------
        combat = self._negative_combat_streak(player_id, now)
        if combat is not None:
            tripped.append("negative_combat_streak")
            detail["negative_combat_streak"] = combat

        # --- 6: economic_loss_streak -------------------------------------
        econ = self._economic_loss_streak(player, now)
        if econ is not None:
            tripped.append("economic_loss_streak")
            detail["economic_loss_streak"] = econ

        # --- 7: social_isolation -----------------------------------------
        social = self._social_isolation(player, now)
        if social is not None:
            tripped.append("social_isolation")
            detail["social_isolation"] = social

        return {"tripped": tripped, "detail": detail}

    # ------------------------------------------------------------------
    # Signal helpers — every method is READ-ONLY (SELECT only)
    # ------------------------------------------------------------------
    def _canonical_days_inactive(
        self, player: Any, now: datetime
    ) -> Optional[int]:
        """Canonical days since last_game_login. None when the player has never
        logged in (we don't flag a never-logged-in account as dormant — it has
        no session history to drift from; mirrors _canonical_days_inactive in
        npc_scheduler_service which returns 0 for NULL, but here None means
        'not applicable' so login-recency signals are skipped entirely)."""
        if player.last_game_login is None:
            return None
        hours = game_time.canonical_hours_since(player.last_game_login, now)
        return max(0, int(hours // 24))

    def _recent_session_durations(self, player_id: Any) -> List[int]:
        """The most-recent COMPLETED sessions' durations (minutes), newest first.
        Only ended sessions (duration_minutes IS NOT NULL) count — an in-flight
        session has no length yet. READ-ONLY."""
        from src.models.player_analytics import PlayerSession

        rows = (
            self.db.query(PlayerSession.duration_minutes)
            .filter(
                PlayerSession.player_id == player_id,
                PlayerSession.duration_minutes.isnot(None),
            )
            .order_by(PlayerSession.start_time.desc())
            .limit(DECLINING_MIN_SESSIONS)
            .all()
        )
        return [int(r[0]) for r in rows if r[0] is not None]

    def _declining_session_length(
        self, durations_newest_first: List[int]
    ) -> Optional[Dict[str, Any]]:
        """canon: "Last 5 sessions trending down (>30% drop)".

        Needs the full window of DECLINING_MIN_SESSIONS completed sessions.
        "Trending down" = the most-recent session is >30% shorter than the
        oldest in the window AND the series is (weakly) monotonically
        non-increasing newest-relative-to-oldest (NO-CANON tie-break: we require
        a genuine downward trend across the window, not a single dip, so a
        bouncy series doesn't trip). Returns evidence dict, or None.
        """
        if len(durations_newest_first) < DECLINING_MIN_SESSIONS:
            return None
        newest = durations_newest_first[0]
        oldest = durations_newest_first[-1]
        if oldest <= 0:
            return None
        drop = (oldest - newest) / oldest
        if drop <= DECLINING_DROP_PCT:
            return None
        # chronological order (oldest → newest) for the monotonic check
        chrono = list(reversed(durations_newest_first))
        non_increasing = all(
            chrono[i + 1] <= chrono[i] for i in range(len(chrono) - 1)
        )
        if not non_increasing:
            return None
        return {
            "threshold_drop_pct": DECLINING_DROP_PCT,
            "observed_drop_pct": round(drop, 3),
            "window_sessions": DECLINING_MIN_SESSIONS,
            "durations_oldest_to_newest": chrono,
        }

    def _early_logout_streak(
        self, durations_newest_first: List[int]
    ) -> Optional[Dict[str, Any]]:
        """canon: "3 consecutive sessions < 5 minutes". The 3 most-recent
        completed sessions must each be under EARLY_LOGOUT_MINUTES."""
        if len(durations_newest_first) < EARLY_LOGOUT_STREAK:
            return None
        streak = durations_newest_first[:EARLY_LOGOUT_STREAK]
        if all(d < EARLY_LOGOUT_MINUTES for d in streak):
            return {
                "threshold_minutes": EARLY_LOGOUT_MINUTES,
                "streak_len": EARLY_LOGOUT_STREAK,
                "observed_recent_minutes": streak,
            }
        return None

    def _negative_combat_streak(
        self, player_id: Any, now: datetime
    ) -> Optional[Dict[str, Any]]:
        """canon: "Recent kill/death ratio inverted" → in the recent window the
        player LOST more decisive combats than they won (kills < deaths). This
        counts ALL of the player's decisive combats (PvP and PvE — vs NPCs /
        sector drones / planet / port-owner defenders), which is canon-consistent:
        the canon names a kill/death ratio, not PvP-only (reviewer LOW — widened
        framing, no PvP restriction). CombatLog is the authoritative combat-outcome
        record (its ``outcome`` column); PlayerActivity logs that combat happened
        but not who won, so the ratio must come from CombatLog. READ-ONLY on CombatLog.

        A "kill" = this player was the winning side; a "death" = the losing side.
        ``escaped`` / ``draw`` count as neither. Requires >= COMBAT_MIN_EVENTS
        decisive combats in-window before the ratio is meaningful (NO-CANON
        floor — avoids flagging a player off a single bad fight).
        """
        from src.models.combat_log import CombatLog

        cutoff = self._canonical_cutoff(now, COMBAT_LOOKBACK_DAYS)
        rows = (
            self.db.query(
                CombatLog.attacker_id,
                CombatLog.defender_id,
                CombatLog.outcome,
            )
            .filter(
                CombatLog.timestamp >= cutoff,
                or_(
                    CombatLog.attacker_id == player_id,
                    CombatLog.defender_id == player_id,
                ),
            )
            .all()
        )
        kills = 0
        deaths = 0
        for attacker_id, defender_id, outcome in rows:
            is_attacker = attacker_id == player_id
            if outcome == "attacker_win":
                kills += 1 if is_attacker else 0
                deaths += 0 if is_attacker else 1
            elif outcome == "defender_win":
                deaths += 1 if is_attacker else 0
                kills += 0 if is_attacker else 1
            # draw / escaped → neither
        decisive = kills + deaths
        if decisive < COMBAT_MIN_EVENTS:
            return None
        if deaths > kills:
            return {
                "lookback_days": COMBAT_LOOKBACK_DAYS,
                "min_events": COMBAT_MIN_EVENTS,
                "kills": kills,
                "deaths": deaths,
                "no_canon": ["lookback_days", "min_events"],
            }
        return None

    def _economic_loss_streak(
        self, player: Any, now: datetime
    ) -> Optional[Dict[str, Any]]:
        """canon: "Recent net credit loss > 50% of holdings".

        Net credit delta over the recent window from PlayerActivity: SELLs add
        ``credits_involved``, BUYs subtract it (trade events carry the credit
        flow; see PlayerActivityService.track_activity). A net loss whose
        magnitude exceeds 50% of the player's CURRENT holdings (Player.credits)
        trips the signal. READ-ONLY on PlayerActivity / Player.

        "Holdings" = Player.credits (current liquid balance). A player at 0
        credits can't lose >50% of 0 meaningfully, so a zero/negative holdings
        baseline does not trip (NO-CANON guard against div-by-zero).
        """
        from src.models.player_analytics import PlayerActivity

        cutoff = self._canonical_cutoff(now, ECONOMIC_LOOKBACK_DAYS)
        rows = (
            self.db.query(
                PlayerActivity.activity_type,
                PlayerActivity.credits_involved,
            )
            .filter(
                PlayerActivity.player_id == player.id,
                PlayerActivity.timestamp >= cutoff,
                PlayerActivity.activity_type.in_(("trade_buy", "trade_sell")),
            )
            .all()
        )
        net = 0
        for activity_type, credits_involved in rows:
            amount = int(credits_involved or 0)
            if activity_type == "trade_sell":
                net += amount
            elif activity_type == "trade_buy":
                net -= amount
        holdings = int(player.credits or 0)
        if holdings <= 0:
            return None
        if net < 0 and abs(net) > ECONOMIC_LOSS_PCT * holdings:
            return {
                "threshold_pct_of_holdings": ECONOMIC_LOSS_PCT,
                "lookback_days": ECONOMIC_LOOKBACK_DAYS,
                "net_credits": net,
                "holdings": holdings,
                "no_canon": ["lookback_days"],
            }
        return None

    def _social_isolation(
        self, player: Any, now: datetime
    ) -> Optional[Dict[str, Any]]:
        """canon: "Solo player with no team / messages in 14 days".

        Trips when BOTH hold: the player is teamless (Player.team_id IS NULL)
        AND they neither sent nor received any Message in the last
        SOCIAL_ISOLATION_DAYS canonical days. READ-ONLY on Player / Message.

        (Message.sent_at is naive UTC; compared against a naive cutoff.)
        """
        from src.models.message import Message

        if player.team_id is not None:
            return None  # on a team → not socially isolated

        cutoff = self._canonical_cutoff(now, SOCIAL_ISOLATION_DAYS)
        # Message.sent_at is a naive DateTime — compare against naive UTC.
        cutoff_naive = cutoff.replace(tzinfo=None)
        recent_msg = (
            self.db.query(Message.id)
            .filter(
                Message.sent_at >= cutoff_naive,
                or_(
                    Message.sender_id == player.id,
                    Message.recipient_id == player.id,
                ),
            )
            .first()
        )
        if recent_msg is not None:
            return None  # had social contact in-window
        return {
            "threshold_days": SOCIAL_ISOLATION_DAYS,
            "teamless": True,
            "messages_in_window": 0,
        }

    # ------------------------------------------------------------------
    # Time helper
    # ------------------------------------------------------------------
    @staticmethod
    def _canonical_cutoff(now: datetime, canonical_days: int) -> datetime:
        """The WALL-CLOCK instant `canonical_days` canonical days before `now`.

        Activity timestamps are stored wall-clock; to ask "in the last N
        canonical days" we convert that canonical span back to wall-clock by
        dividing by GAME_TIME_SCALE (the inverse of canonical_hours_since), so
        the window is observable on a time-compressed dev stack exactly like the
        day-gated sweeps. (At GAME_TIME_SCALE=1.0 this is just N calendar days.)
        """
        from datetime import timedelta

        wall_seconds = canonical_days * 86400 / game_time.GAME_TIME_SCALE
        return now - timedelta(seconds=wall_seconds)
