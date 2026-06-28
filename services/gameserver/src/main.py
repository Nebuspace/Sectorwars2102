"""
Sectorwars 2102 Game Server - Main FastAPI Application
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from src.api.api import api_router
from src.core.config import settings
from src.core.database import Base, async_engine
from src.utils.error_handling import setup_error_handling

# Configure logging
logging.basicConfig(
    level=logging.INFO if not settings.DEBUG else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI 0.115+ lifespan handler (replaces @app.on_event startup/shutdown).

    Runs DB schema init, admin user bootstrap, the WebSocket heartbeat cleanup
    background task, and the bang job orphan recovery sweep on startup; logs
    a shutdown line on teardown.
    """
    # ---------- startup ----------
    logger.info("Starting Sectorwars 2102 Game Server...")

    try:
        # Create database tables
        async with async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables initialized")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")

    # Initialize default admin user if needed
    try:
        from src.auth.admin import create_default_admin, create_default_factions
        from src.core.database import SessionLocal

        db = SessionLocal()
        try:
            create_default_admin(db)
            logger.info("Admin user initialization completed")
            # WO-E: seed the canon faction roster at startup (idempotent per
            # faction_type; coexists with npc_spawn_service._ensure_federation_faction).
            create_default_factions(db)
            logger.info("Faction roster seed completed")
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Admin user initialization failed: {e}")
        # Don't crash the server if admin creation fails

    # WO-BO / ADR-0079: backfill archetype-driven trader personalities onto
    # existing stations (the BORDER model-default makes haggling difficulty a
    # no-op). Idempotent + boot-safe — never raises; rows already class-correct
    # are skipped, real per-player haggle memory is preserved.
    try:
        from src.core.database import SessionLocal
        from src.services.haggle_service import seed_trader_personalities

        db = SessionLocal()
        try:
            summary = seed_trader_personalities(db)
            logger.info(
                "Trader-personality seed: scanned=%s reseeded=%s",
                summary.get("scanned"), summary.get("reseeded"),
            )
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Trader-personality seeding failed (non-fatal): {e}")

    # i18n auto-seed: ensure default languages/namespaces exist and load any
    # JSON translation bundles shipped with the gameserver image. Files live
    # at /app/i18n/{lng}/{ns}.json. Re-runs are no-ops (overwrite=False), so
    # this is safe on every boot. Without this, a fresh DB serves empty
    # namespaces and admin UI renders raw t-keys (see #317).
    try:
        import json
        from pathlib import Path

        from src.core.database import SessionLocal
        from src.services.translation_service import TranslationService

        i18n_root = Path(__file__).resolve().parent.parent / "i18n"
        db = SessionLocal()
        try:
            tservice = TranslationService(db)
            await tservice.initialize_default_data()

            bundle_files = sorted(i18n_root.glob("*/*.json")) if i18n_root.is_dir() else []
            for path in bundle_files:
                language_code = path.parent.name
                namespace = path.stem
                try:
                    with path.open() as fh:
                        bundle = json.load(fh)
                    summary = await tservice.bulk_import_translations(
                        translations=bundle,
                        language_code=language_code,
                        namespace=namespace,
                        overwrite=False,
                    )
                    logger.info(
                        "i18n seed %s/%s: imported=%s skipped=%s errors=%s",
                        language_code, namespace,
                        summary.get("imported"), summary.get("skipped"), summary.get("errors"),
                    )
                except Exception as inner:
                    logger.warning("i18n seed failed for %s: %s", path, inner)
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"i18n auto-seed skipped: {e}")

    # Ship specifications auto-seed: shipyard ship creation reads
    # ShipSpecification rows and raises ValueError when a type is missing,
    # so a clean DB without this seed cannot sell ships. Idempotent — the
    # seeder updates existing rows in place, so re-runs are safe on every
    # boot (mirrors the i18n seed pattern above).
    try:
        from src.core.database import SessionLocal
        from src.core.ship_specifications_seeder import seed_ship_specifications

        db = SessionLocal()
        try:
            seed_ship_specifications(db)
            logger.info("Ship specifications seed completed")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Ship specifications seed skipped: {e}")

    # Medal catalog seed (ADR-0028). The relational `medals` table is the
    # canonical award catalog; without this seed an empty catalog makes every
    # award no-op (unknown medal_id). Idempotent upsert, same pattern as ships.
    try:
        from src.core.database import SessionLocal
        from src.services.medal_catalog import seed_medals

        db = SessionLocal()
        try:
            seed_medals(db)
            logger.info("Medal catalog seed completed")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Medal catalog seed skipped: {e}")

    # Start WebSocket heartbeat cleanup background task
    import asyncio
    async def _heartbeat_cleanup_loop():
        """Periodically disconnect stale WebSocket connections."""
        from src.services.websocket_service import connection_manager
        while True:
            await asyncio.sleep(30)
            try:
                await connection_manager.cleanup_stale_connections(timeout_seconds=300)
            except Exception as e:
                logger.warning(f"Heartbeat cleanup error: {e}")

    heartbeat_task = asyncio.create_task(_heartbeat_cleanup_loop())

    # NPC scheduler (Living NPC System — Loops A/B/C). Env-gated so prod
    # stays static until the system is proven on dev. Tick bodies run in
    # a worker thread (asyncio.to_thread) so the event loop never blocks.
    npc_scheduler_task = None
    if settings.NPC_SCHEDULER_ENABLED:
        from src.services.npc_scheduler_service import npc_scheduler_loop
        npc_scheduler_task = asyncio.create_task(npc_scheduler_loop())
        logger.info("NPC scheduler task started (NPC_SCHEDULER_ENABLED=true)")
    else:
        logger.info("NPC scheduler disabled (NPC_SCHEDULER_ENABLED=false)")

    # Orphan-recovery sweep: mark any bang generation jobs left in RUNNING
    # state for >5 minutes as FAILED. See DOCS/PLANS/bang-integration.md
    # § Phase 1B. Safe to run before migrations land (errors are logged
    # and the server keeps going).
    try:
        from datetime import timedelta

        from sqlalchemy import func as sa_func
        from sqlalchemy import update

        from src.core.database import AsyncSessionLocal
        from src.models.bang_generation_job import (
            BangGenerationJob,
            BangGenerationJobStatus,
        )

        async with AsyncSessionLocal() as session:
            stmt = (
                update(BangGenerationJob)
                .where(BangGenerationJob.status == BangGenerationJobStatus.RUNNING)
                .where(
                    BangGenerationJob.started_at
                    < sa_func.now() - timedelta(minutes=5)
                )
                .values(
                    status=BangGenerationJobStatus.FAILED,
                    error_message="orphaned at startup",
                    completed_at=sa_func.now(),
                )
            )
            result = await session.execute(stmt)
            await session.commit()
            recovered = result.rowcount or 0
            if recovered:
                logger.info(
                    "Bang job orphan recovery: marked %d stale RUNNING jobs as FAILED",
                    recovered,
                )
            else:
                logger.debug("Bang job orphan recovery: no stale jobs found")
    except Exception as e:
        # Table may not exist on first boot before migrations run; failing
        # the startup hook would block the whole server. Log + move on.
        logger.warning(f"Bang job orphan recovery skipped: {e}")

    logger.info("Sectorwars 2102 Game Server started successfully")

    # ---------- yield to application ----------
    yield

    # ---------- shutdown ----------
    logger.info("Shutting down Sectorwars 2102 Game Server...")
    for task, label in ((heartbeat_task, "Heartbeat cleanup"),
                        (npc_scheduler_task, "NPC scheduler")):
        if task is None:
            continue
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # Expected: we just cancelled it.
            logger.debug(f"{label} task cancelled cleanly")
        except Exception as e:  # noqa: BLE001 — best-effort shutdown
            logger.warning(f"{label} raised during shutdown (ignored): {e}")

# Create FastAPI application
app = FastAPI(
    title="Sectorwars 2102 Game Server",
    description="Advanced space trading simulation game server with AI intelligence",
    version="2.1.0",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    openapi_url="/openapi.json" if settings.DEBUG else None,
    redirect_slashes=True,  # Auto-redirect /path/ to /path (eliminates duplicate route definitions)
    lifespan=lifespan,
)

# Security middleware
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["*"] if settings.DEVELOPMENT_MODE else ["localhost", "*.app.github.dev", "*.repl.co"]
)

# CORS middleware
allowed_origins = ["*"] if settings.DEVELOPMENT_MODE else [
    settings.get_frontend_url(),
    "https://*.app.github.dev",
    "https://*.repl.co"
]

# Always allow localhost origins for development
if settings.DEVELOPMENT_MODE:
    allowed_origins = ["*"]
else:
    allowed_origins.extend([
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001"
    ])

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)

# Galaxy-state guard: blocks player traffic with 503 while a bang
# generation job is mid-flight. Admin and auth paths bypass the check.
# Fails-open (logs + lets traffic through) if the lookup raises — keeps
# the server usable on fresh environments before migrations land.
from src.middleware.galaxy_state_guard import GalaxyStateGuardMiddleware  # noqa: E402

app.add_middleware(GalaxyStateGuardMiddleware)

# Include API router
app.include_router(api_router, prefix=settings.API_V1_STR)

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "status_code": exc.status_code}
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle validation errors"""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors(), "body": exc.body}
    )

@app.exception_handler(SQLAlchemyError)
async def database_exception_handler(request: Request, exc: SQLAlchemyError):
    """Handle database errors"""
    logger.error(f"Database error: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Database error occurred"}
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle all other exceptions"""
    logger.error(f"Unexpected error: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"}
    )

async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Shutting down Sectorwars 2102 Game Server...")

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Sectorwars 2102 Game Server",
        "version": "2.1.0",
        "status": "online",
        "timestamp": datetime.utcnow().isoformat(),
        "api_docs": "/docs" if settings.DEBUG else "disabled",
        "environment": settings.detect_environment()
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        # Test database connection
        from src.core.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            from sqlalchemy import text
            await session.execute(text("SELECT 1"))

        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "database": "connected",
            "environment": settings.detect_environment(),
            "api_version": settings.API_V1_STR
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unhealthy",
                "timestamp": datetime.utcnow().isoformat(),
                "database": "disconnected",
                # Don't leak traceback to health probes
                # (py/stack-trace-exposure).
                "error": "Database health check failed",
            },
        )

# Setup error handling
setup_error_handling(app)

# Setup security middleware (rate limiting, input validation, security headers, audit logging)
try:
    from src.api.middleware.security import setup_security_middleware
    setup_security_middleware(app)
    logger.info("Security middleware registered successfully")
except Exception as e:
    logger.warning(f"Failed to register security middleware: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",  # noqa: S104 — container needs to bind to all interfaces
        port=8080,
        reload=settings.DEBUG,
        log_level="info"
    )
