"""
Admin Analytics & Reports — read-only aggregates.

Serves five endpoints consumed by the admin-ui Advanced Analytics page:
  GET  /admin/reports/metrics      — available metric catalog
  GET  /admin/reports/templates    — built-in report templates
  POST /admin/reports/generate     — run a custom report against live DB
  GET  /admin/analytics/export     — bulk data export (json / csv)
  GET  /admin/performance/metrics  — system / DB / application performance snapshot
"""
import csv
import io
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from src.auth.admin_scopes import AUDIT_VIEW
from src.auth.dependencies import require_scope
from src.core.database import get_db
from src.models.combat_log import CombatLog
from src.models.market_transaction import MarketTransaction
from src.models.player import Player
from src.models.sector import Sector
from src.models.ship import Ship
from src.models.team import Team
from src.models.user import User

router = APIRouter(prefix="/admin", tags=["admin-reports"])

# ---------------------------------------------------------------------------
# Pydantic shapes — must match the TypeScript interfaces in the admin-ui
# ---------------------------------------------------------------------------

class ReportFilter(BaseModel):
    id: str
    field: str
    operator: str
    value: Any


class ReportTemplate(BaseModel):
    id: str
    name: str
    description: str
    metrics: List[str]
    filters: List[ReportFilter] = []
    groupBy: List[str] = []
    sortBy: List[Dict[str, str]] = []
    visualization: str = "table"
    chartType: Optional[str] = None
    schedule: Optional[Dict[str, Any]] = None


class ReportResult(BaseModel):
    id: str
    name: str
    generatedAt: str
    data: Any
    template: Any


# ---------------------------------------------------------------------------
# Metric catalog — schema-driven, no DB reads required
# ---------------------------------------------------------------------------

_METRIC_CATALOG = [
    # Player metrics
    {"id": "player_total_count", "name": "Total Players", "category": "Players",
     "dataType": "number", "aggregations": ["count"],
     "description": "Total registered players (excludes soft-deleted accounts)"},
    {"id": "player_active_count", "name": "Active Players (7d)", "category": "Players",
     "dataType": "number", "aggregations": ["count"],
     "description": "Players who logged in within the last 7 days (excludes soft-deleted)"},
    {"id": "player_avg_credits", "name": "Avg Player Credits", "category": "Players",
     "dataType": "currency", "aggregations": ["avg"],
     "description": "Average credits held across non-deleted players"},
    {"id": "player_total_credits", "name": "Total Credits in Circulation", "category": "Players",
     "dataType": "currency", "aggregations": ["sum"],
     "description": "Sum of credits held by non-deleted players"},
    {"id": "player_avg_turns", "name": "Avg Turns Remaining", "category": "Players",
     "dataType": "number", "aggregations": ["avg"],
     "description": "Average turns remaining across non-deleted players"},
    # Economy metrics
    {"id": "market_total_transactions", "name": "Total Market Transactions", "category": "Economy",
     "dataType": "number", "aggregations": ["count"],
     "description": "Total number of market trades executed"},
    {"id": "market_total_volume", "name": "Total Trade Volume (credits)", "category": "Economy",
     "dataType": "currency", "aggregations": ["sum"],
     "description": "Sum of all transaction values in credits"},
    {"id": "market_avg_profit_margin", "name": "Avg Profit Margin %", "category": "Economy",
     "dataType": "percentage", "aggregations": ["avg"],
     "description": "Average recorded profit margin across all market transactions"},
    # Sector metrics
    {"id": "sector_total_count", "name": "Total Sectors", "category": "Galaxy",
     "dataType": "number", "aggregations": ["count"],
     "description": "Total sectors in the galaxy"},
    # Combat metrics
    {"id": "combat_total_encounters", "name": "Total Combat Encounters", "category": "Combat",
     "dataType": "number", "aggregations": ["count"],
     "description": "Total logged combat events"},
    # Fleet metrics
    {"id": "ship_total_count", "name": "Total Ships", "category": "Fleet",
     "dataType": "number", "aggregations": ["count"],
     "description": "Total ships registered in the game"},
    # Team metrics
    {"id": "team_total_count", "name": "Total Teams", "category": "Teams",
     "dataType": "number", "aggregations": ["count"],
     "description": "Total teams formed by players"},
]

# Built-in templates returned by GET /admin/reports/templates
_BUILTIN_TEMPLATES = [
    {
        "id": "tpl-economy-overview",
        "name": "Economy Overview",
        "description": "Key economic indicators: trade volume, credits in circulation, profit margins",
        "metrics": ["market_total_transactions", "market_total_volume", "market_avg_profit_margin",
                    "player_total_credits"],
        "filters": [],
        "groupBy": [],
        "sortBy": [],
        "visualization": "table",
    },
    {
        "id": "tpl-player-health",
        "name": "Player Health",
        "description": "Player engagement snapshot: total, active, and resource distribution",
        "metrics": ["player_total_count", "player_active_count", "player_avg_credits",
                    "player_avg_turns"],
        "filters": [],
        "groupBy": [],
        "sortBy": [],
        "visualization": "table",
    },
    {
        "id": "tpl-galaxy-status",
        "name": "Galaxy Status",
        "description": "Galaxy-wide summary: sectors, ships, teams, and combat activity",
        "metrics": ["sector_total_count", "ship_total_count", "team_total_count",
                    "combat_total_encounters"],
        "filters": [],
        "groupBy": [],
        "sortBy": [],
        "visualization": "table",
    },
]


# ---------------------------------------------------------------------------
# Helper — compute a single metric value against the live DB
# ---------------------------------------------------------------------------

def _players_excluding_soft_deleted(db: Session):
    """Player rows whose owning User is not soft-deleted.

    Must stay aligned with GET /admin/analytics/export?dataset=players
    (which already filters ``User.deleted == False``).
    """
    return (
        db.query(Player)
        .join(User, User.id == Player.user_id)
        .filter(User.deleted == False)  # noqa: E712 — SQLAlchemy column compare
    )


def _compute_metric(metric_id: str, db: Session) -> Any:
    """Return the real DB-aggregate value for a given metric ID, or 0 if
    the metric ID is unknown.  Never fabricates data.

    Player-facing aggregates exclude soft-deleted accounts so metrics match
    the players export filter (calibration reconcile).
    """
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    live_players = _players_excluding_soft_deleted(db)

    if metric_id == "player_total_count":
        return live_players.with_entities(func.count(Player.id)).scalar() or 0

    if metric_id == "player_active_count":
        return (
            live_players
            .filter(User.last_login >= week_ago)
            .with_entities(func.count(User.id))
            .scalar()
            or 0
        )

    if metric_id == "player_avg_credits":
        result = live_players.with_entities(func.avg(Player.credits)).scalar()
        return round(float(result), 2) if result else 0.0

    if metric_id == "player_total_credits":
        return live_players.with_entities(func.sum(Player.credits)).scalar() or 0

    if metric_id == "player_avg_turns":
        result = live_players.with_entities(func.avg(Player.turns)).scalar()
        return round(float(result), 2) if result else 0.0

    if metric_id == "market_total_transactions":
        return db.query(func.count(MarketTransaction.id)).scalar() or 0

    if metric_id == "market_total_volume":
        return db.query(func.sum(MarketTransaction.total_value)).scalar() or 0

    if metric_id == "market_avg_profit_margin":
        result = (
            db.query(func.avg(MarketTransaction.profit_margin))
            .filter(MarketTransaction.profit_margin.isnot(None))
            .scalar()
        )
        return round(float(result), 2) if result else 0.0

    if metric_id == "sector_total_count":
        return db.query(func.count(Sector.id)).scalar() or 0

    if metric_id == "combat_total_encounters":
        return db.query(func.count(CombatLog.id)).scalar() or 0

    if metric_id == "ship_total_count":
        # NPC ships + ships owned by non-deleted players (exclude soft-deleted owners).
        return (
            db.query(func.count(Ship.id))
            .outerjoin(Player, Player.id == Ship.owner_id)
            .outerjoin(User, User.id == Player.user_id)
            .filter(
                (Ship.is_npc == True)  # noqa: E712
                | (Ship.owner_id.is_(None))
                | (User.deleted == False)  # noqa: E712
            )
            .scalar()
            or 0
        )

    if metric_id == "team_total_count":
        return db.query(func.count(Team.id)).scalar() or 0

    return 0  # unknown metric — return zero rather than error


# ---------------------------------------------------------------------------
# Endpoint 1: GET /admin/reports/metrics
# ---------------------------------------------------------------------------

@router.get("/reports/metrics")
def get_report_metrics(
    admin: User = Depends(require_scope(AUDIT_VIEW)),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Return the available metric catalog for the report builder."""
    return {"metrics": _METRIC_CATALOG}


# ---------------------------------------------------------------------------
# Endpoint 2: GET /admin/reports/templates
# ---------------------------------------------------------------------------

@router.get("/reports/templates")
def get_report_templates(
    admin: User = Depends(require_scope(AUDIT_VIEW)),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Return built-in report templates."""
    return {"templates": _BUILTIN_TEMPLATES}


# ---------------------------------------------------------------------------
# Endpoint 3: POST /admin/reports/generate
# ---------------------------------------------------------------------------

@router.post("/reports/generate")
def generate_report(
    template: ReportTemplate,
    admin: User = Depends(require_scope(AUDIT_VIEW)),
    db: Session = Depends(get_db),
) -> ReportResult:
    """
    Execute a report by computing real DB aggregates for each requested metric.
    Returns a ReportResult whose data dict maps metric_id → value.
    """
    if not template.metrics:
        raise HTTPException(status_code=400, detail="At least one metric must be requested")
    if len(template.metrics) > 50:
        raise HTTPException(status_code=400, detail="Too many metrics requested (max 50)")

    data: Dict[str, Any] = {}
    for metric_id in template.metrics:
        data[metric_id] = _compute_metric(metric_id, db)

    return ReportResult(
        id=f"report-{uuid.uuid4().hex[:12]}",
        name=template.name,
        generatedAt=datetime.now(timezone.utc).isoformat(),
        data=data,
        template=template.model_dump(),
    )


# ---------------------------------------------------------------------------
# Endpoint 4: GET /admin/analytics/export
# ---------------------------------------------------------------------------

@router.get("/analytics/export")
def export_data(
    dataset: str = Query(..., description="Dataset to export: players, economy, combat, teams, ships, performance"),
    format: str = Query("json", description="Output format: json or csv"),
    admin: User = Depends(require_scope(AUDIT_VIEW)),
    db: Session = Depends(get_db),
) -> Response:
    """
    Bulk data export.  json and csv formats are supported.
    excel and pdf are not available without additional server-side dependencies.
    """
    if format not in ("json", "csv"):
        raise HTTPException(
            status_code=400,
            detail=f"Format '{format}' is not supported server-side. Use json or csv. "
                   "(excel/pdf generation requires additional dependencies not installed.)",
        )

    rows: List[Dict[str, Any]] = []

    if dataset == "players":
        records = (
            db.query(Player, User)
            .join(User, User.id == Player.user_id)
            .filter(User.deleted == False)
            .limit(10000)
            .all()
        )
        rows = [
            {
                "player_id": str(p.id),
                "username": u.username,
                "nickname": p.nickname or "",
                "credits": p.credits,
                "turns": p.turns,
                "personal_reputation": p.personal_reputation,
                "reputation_tier": p.reputation_tier,
                "military_rank": p.military_rank,
                "is_docked": p.is_docked,
                "is_landed": p.is_landed,
                "created_at": u.created_at.isoformat() if u.created_at else "",
                "last_login": u.last_login.isoformat() if u.last_login else "",
            }
            for p, u in records
        ]

    elif dataset == "economy":
        records = (
            db.query(MarketTransaction)
            .order_by(MarketTransaction.timestamp.desc())
            .limit(10000)
            .all()
        )
        rows = [
            {
                "transaction_id": str(t.id),
                "player_id": str(t.player_id) if t.player_id else "",
                "station_id": str(t.station_id) if t.station_id else "",
                "transaction_type": t.transaction_type.value,
                "commodity": t.commodity,
                "quantity": t.quantity,
                "unit_price": t.unit_price,
                "total_value": t.total_value,
                "profit_margin": t.profit_margin or 0.0,
                "sector_id": t.sector_id or "",
                "timestamp": t.timestamp.isoformat() if t.timestamp else "",
            }
            for t in records
        ]

    elif dataset == "combat":
        records = (
            db.query(CombatLog)
            .order_by(CombatLog.timestamp.desc())
            .limit(10000)
            .all()
        )
        rows = [
            {
                "combat_id": str(c.id),
                "attacker_id": str(c.attacker_id) if c.attacker_id else "",
                "defender_id": str(c.defender_id) if c.defender_id else "",
                "attacker_ship_name": c.attacker_ship_name or "",
                "defender_ship_name": c.defender_ship_name or "",
                "attacker_ship_type": c.attacker_ship_type or "",
                "defender_ship_type": c.defender_ship_type or "",
                "sector_id": c.sector_id or "",
                "timestamp": c.timestamp.isoformat() if c.timestamp else "",
            }
            for c in records
        ]

    elif dataset == "teams":
        records = db.query(Team).limit(10000).all()
        rows = [
            {
                "team_id": str(t.id),
                "name": t.name,
                "description": getattr(t, "description", "") or "",
                "created_at": t.created_at.isoformat() if getattr(t, "created_at", None) else "",
            }
            for t in records
        ]

    elif dataset == "ships":
        # Match ship_total_count metric: NPCs + unowned + owned by non-deleted users.
        records = (
            db.query(Ship)
            .outerjoin(Player, Player.id == Ship.owner_id)
            .outerjoin(User, User.id == Player.user_id)
            .filter(
                (Ship.is_npc == True)  # noqa: E712
                | (Ship.owner_id.is_(None))
                | (User.deleted == False)  # noqa: E712
            )
            .limit(10000)
            .all()
        )
        rows = [
            {
                "ship_id": str(s.id),
                "name": s.name,
                "ship_type": s.type.value if s.type else "",
                "owner_id": str(s.owner_id) if s.owner_id else "",
                "sector_id": s.sector_id or "",
                "is_npc": s.is_npc,
                "status": s.status.value if s.status else "",
            }
            for s in records
        ]

    elif dataset == "performance":
        # Redirect — return the same aggregates as the performance/metrics endpoint
        rows = [{"note": "Use GET /api/v1/admin/performance/metrics for structured performance data."}]

    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown dataset '{dataset}'. Valid options: players, economy, combat, teams, ships, performance.",
        )

    # Serialize
    if format == "json":
        import json
        content = json.dumps({"dataset": dataset, "count": len(rows), "rows": rows}, indent=2, default=str)
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{dataset}-export.json"'},
        )

    # CSV
    if not rows:
        return Response(
            content="",
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{dataset}-export.csv"'},
        )
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{dataset}-export.csv"'},
    )


# ---------------------------------------------------------------------------
# Endpoint 5: GET /admin/performance/metrics
# ---------------------------------------------------------------------------

_TIME_RANGE_HOURS = {"1h": 1, "6h": 6, "24h": 24, "7d": 168}


@router.get("/performance/metrics")
def get_performance_metrics(
    timeRange: str = Query("24h", description="Time window: 1h, 6h, 24h, or 7d"),
    admin: User = Depends(require_scope(AUDIT_VIEW)),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Performance snapshot.

    - System metrics (CPU load, memory, disk, network): NOT available from
      Python without psutil; returned as 0.0.  Only activeConnections and
      uptime are sourced from Postgres.
    - Database metrics: sourced from pg_stat_activity and pg_statio_all_tables.
    - Application metrics: sourced from MarketTransaction aggregates as a
      proxy for throughput; response-time percentiles are not tracked in-band
      and are returned as 0.
    - Historical: transaction volume over the requested window, bucketed into
      12 evenly-spaced samples.
    - Suggestions: static, evidence-based recommendations for this stack.
    """
    if timeRange not in _TIME_RANGE_HOURS:
        timeRange = "24h"
    window_hours = _TIME_RANGE_HOURS[timeRange]
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    # --- DB-sourceable system metrics ---
    try:
        active_conn_row = db.execute(
            text("SELECT count(*) FROM pg_stat_activity WHERE state IS NOT NULL")
        ).fetchone()
        active_connections = int(active_conn_row[0]) if active_conn_row else 0
    except Exception:
        active_connections = 0

    try:
        uptime_row = db.execute(
            text("SELECT extract(epoch FROM now() - pg_postmaster_start_time()) AS uptime_s")
        ).fetchone()
        uptime_seconds = float(uptime_row[0]) if uptime_row and uptime_row[0] else 0.0
        # Express as % of 30-day rolling window; capped at 100
        uptime_pct = min(100.0, round(uptime_seconds / (30 * 86400) * 100, 4))
    except Exception:
        uptime_pct = 0.0

    # --- Throughput proxy: transactions in the last minute ---
    try:
        one_min_ago = datetime.now(timezone.utc) - timedelta(minutes=1)
        txn_last_min = (
            db.query(func.count(MarketTransaction.id))
            .filter(MarketTransaction.timestamp >= one_min_ago)
            .scalar()
            or 0
        )
        rps = round(txn_last_min / 60.0, 3)
    except Exception:
        rps = 0.0

    # --- DB connection pool breakdown ---
    try:
        pool_rows = db.execute(
            text(
                "SELECT state, count(*) AS cnt FROM pg_stat_activity "
                "WHERE state IS NOT NULL GROUP BY state"
            )
        ).fetchall()
        pool_by_state: Dict[str, int] = {r[0]: int(r[1]) for r in pool_rows}
    except Exception:
        pool_by_state = {}
    pool_active = pool_by_state.get("active", 0)
    pool_idle = pool_by_state.get("idle", 0) + pool_by_state.get("idle in transaction", 0)
    pool_total = active_connections

    # --- Cache hit rate ---
    try:
        cache_row = db.execute(
            text(
                "SELECT "
                "  sum(heap_blks_hit) AS hits, "
                "  sum(heap_blks_hit) + sum(heap_blks_read) AS total "
                "FROM pg_statio_all_tables"
            )
        ).fetchone()
        if cache_row and cache_row[1] and cache_row[1] > 0:
            cache_hit_rate = round(float(cache_row[0]) / float(cache_row[1]) * 100, 2)
        else:
            cache_hit_rate = 0.0
    except Exception:
        cache_hit_rate = 0.0

    # --- Slow queries (>1 s) ---
    try:
        slow_q_row = db.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE state = 'active' "
                "AND now() - query_start > interval '1 second'"
            )
        ).fetchone()
        slow_queries = int(slow_q_row[0]) if slow_q_row else 0
    except Exception:
        slow_queries = 0

    # --- Historical: transaction count bucketed over the window ---
    num_buckets = 12
    bucket_size = timedelta(hours=window_hours / num_buckets)
    timestamps: List[str] = []
    hist_server_load: List[float] = []
    hist_response_time: List[float] = []
    hist_error_rate: List[float] = []

    for i in range(num_buckets):
        bucket_start = since + bucket_size * i
        bucket_end = bucket_start + bucket_size
        try:
            bucket_count = (
                db.query(func.count(MarketTransaction.id))
                .filter(
                    MarketTransaction.timestamp >= bucket_start,
                    MarketTransaction.timestamp < bucket_end,
                )
                .scalar()
                or 0
            )
        except Exception:
            bucket_count = 0
        timestamps.append(bucket_start.strftime("%H:%M" if window_hours <= 24 else "%m/%d %H:%M"))
        # server_load: not available → 0; response_time: use count as a rough proxy (more txns ≈ more work)
        hist_server_load.append(0.0)
        hist_response_time.append(float(bucket_count))
        hist_error_rate.append(0.0)

    # --- Top endpoints by transaction table (proxy only) ---
    try:
        endpoint_rows = (
            db.query(
                MarketTransaction.commodity,
                func.count(MarketTransaction.id).label("calls"),
                func.avg(MarketTransaction.total_value).label("avg_value"),
            )
            .filter(MarketTransaction.timestamp >= since)
            .group_by(MarketTransaction.commodity)
            .order_by(func.count(MarketTransaction.id).desc())
            .limit(5)
            .all()
        )
        endpoints = [
            {
                "path": f"/trading/{row.commodity.lower()}",
                "avgTime": 0,
                "calls": row.calls,
                "errors": 0,
            }
            for row in endpoint_rows
        ]
    except Exception:
        endpoints = []

    # --- Application-level aggregates ---
    try:
        total_txns = (
            db.query(func.count(MarketTransaction.id))
            .filter(MarketTransaction.timestamp >= since)
            .scalar()
            or 0
        )
        throughput = round(total_txns / max(window_hours * 3600, 1), 4)
    except Exception:
        throughput = 0.0

    return {
        "system": {
            "serverLoad": 0.0,       # not available without psutil
            "memoryUsage": 0.0,      # not available without psutil
            "diskUsage": 0.0,        # not available without psutil
            "networkLatency": 0.0,   # not available without psutil
            "activeConnections": active_connections,
            "requestsPerSecond": rps,
            "errorRate": 0.0,        # no in-band error tracking
            "uptime": uptime_pct,
        },
        "database": {
            "queryTime": 0.0,        # requires pg_stat_statements extension
            "activeQueries": pool_active,
            "slowQueries": slow_queries,
            "connectionPool": {
                "active": pool_active,
                "idle": pool_idle,
                "total": pool_total,
            },
            "cacheHitRate": cache_hit_rate,
        },
        "application": {
            "responseTime": {
                "p50": 0,            # no in-band latency tracking
                "p95": 0,
                "p99": 0,
            },
            "throughput": throughput,
            "errorCount": 0,
            "successRate": 100.0,
            "endpoints": endpoints,
        },
        "historical": {
            "timestamps": timestamps,
            "serverLoad": hist_server_load,
            "responseTime": hist_response_time,
            "errorRate": hist_error_rate,
        },
        "suggestions": [
            {
                "id": "sug-pg-stat-statements",
                "title": "Enable pg_stat_statements",
                "description": (
                    "Enable the pg_stat_statements Postgres extension to surface real query-time "
                    "percentiles and slow-query data in this dashboard."
                ),
                "impact": "high",
                "effort": "low",
                "category": "Database",
                "estimatedImprovement": "Enables query-level P50/P95/P99 visibility",
            },
            {
                "id": "sug-psutil",
                "title": "Add psutil for host metrics",
                "description": (
                    "Install the psutil package (zero runtime risk, pure Python) to surface real "
                    "CPU load, memory usage, disk usage, and network latency in the System panel."
                ),
                "impact": "medium",
                "effort": "low",
                "category": "Infrastructure",
                "estimatedImprovement": "Fills the 4 currently-zero system metric cards",
            },
            {
                "id": "sug-index-market-timestamp",
                "title": "Index market_transaction.timestamp",
                "description": (
                    "The performance endpoint and several admin analytics queries filter on "
                    "MarketTransaction.timestamp. Confirm a BRIN or BTREE index exists on this "
                    "column to keep time-windowed aggregates fast at scale."
                ),
                "impact": "medium",
                "effort": "low",
                "category": "Database",
                "estimatedImprovement": "Faster analytics window queries",
            },
            {
                "id": "sug-request-middleware",
                "title": "Add request-timing middleware",
                "description": (
                    "Wire a FastAPI middleware that records p50/p95/p99 response times per route "
                    "into an in-memory ring buffer or Redis sorted set. This unblocks the "
                    "response-time percentile cards and the top-endpoints table."
                ),
                "impact": "high",
                "effort": "medium",
                "category": "Application",
                "estimatedImprovement": "Enables real response-time telemetry",
            },
        ],
    }
