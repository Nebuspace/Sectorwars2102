"""
Status routes for checking API health without authentication.
These endpoints are available without authentication and are used
by frontends to check if the API is up and running.
"""
import os
import datetime
import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

from src.core.config import settings
from src.utils.error_handling import generate_error_id

# Create a dedicated status router without authentication
router = APIRouter()

# Standard status response with environment info
def get_status_response():
    environment = os.environ.get("ENVIRONMENT", "development")
    return {
        "message": "Game API Server is operational",
        "environment": environment,
        "status": "healthy",
        "api_version": "v1"
    }

# Status endpoint for health checks
# Registered at both "" and "/" so GET /api/v1/status works without a 307
# trailing-slash redirect — the redirect emits an http:// Location through
# the nginx/cloudflared proxy chain, which browsers on https reject.
@router.get("")
@router.get("/")
async def status_root(request: Request):
    """
    Get the status of the API.
    This endpoint does not require authentication.
    """
    from src.services.websocket_service import connection_manager
    
    host = request.headers.get("host", "")
    origin = request.headers.get("origin", "")
    forwarded_host = request.headers.get("x-forwarded-host", "")
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    
    # Get connection statistics
    connection_stats = connection_manager.get_connection_stats()
    
    response = get_status_response()
    response["active_connections"] = connection_stats["total_connections"]
    response["admin_connections"] = connection_stats["total_admin_connections"]
    response["connection_stats"] = connection_stats
    
    # Include request debugging information in development
    if settings.DEBUG:
        # Strip sensitive headers to prevent credential leakage (BETA-011)
        SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "proxy-authorization", "x-api-key"}
        safe_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in SENSITIVE_HEADERS
        }
        response["debug"] = {
            "host": host,
            "origin": origin,
            "x-forwarded-host": forwarded_host,
            "x-forwarded-proto": forwarded_proto,
            "headers": safe_headers,
            "url": str(request.url),
            "base_url": str(request.base_url),
            "method": request.method,
            "client": request.client.host if request.client else None,
            "timestamp": datetime.datetime.now().isoformat()
        }
    
    return response

# Add a simple ping endpoint that's easy to access
@router.get("/ping")
async def ping():
    """
    Simple ping endpoint for testing connectivity.
    Returns a simple response with no additional processing.
    """
    return {"ping": "pong", "timestamp": datetime.datetime.now().isoformat()}

# Version endpoint
@router.get("/version")
async def api_version():
    """
    Get the version of the API.
    This endpoint does not require authentication.
    """
    return {"version": "0.1.0"}

# Health check endpoint
@router.get("/health")
async def health_check():
    """
    Health check endpoint for monitoring.
    This endpoint does not require authentication.
    """
    return {"status": "healthy", "service": "gameserver"}

# AI Provider Health Check Endpoints
@router.get("/ai/providers")
async def ai_providers_health():
    """
    Check health status of all AI providers.
    This endpoint does not require authentication.
    """
    import time
    from src.services.ai_provider_service import get_ai_provider_service, ProviderType
    
    start_time = time.time()
    service = get_ai_provider_service()
    
    # Check each provider
    providers_status = {}
    
    # OpenAI Health Check
    try:
        openai_start = time.time()
        openai_configured = bool(os.environ.get("OPENAI_API_KEY"))
        openai_available = service.is_ai_available() and ProviderType.OPENAI in service.get_available_providers()
        
        # Test actual connectivity if configured
        openai_reachable = False
        openai_error = None
        openai_error_id = None
        if openai_configured and openai_available:
            try:
                # Quick test with OpenAI API
                import openai
                client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
                # Use a minimal request to test connectivity
                response = client.models.list()
                openai_reachable = True
            except Exception as e:
                openai_error_id = generate_error_id()
                logger.error(
                    "OpenAI health-check connectivity test failed [error_id=%s]",
                    openai_error_id, exc_info=True,
                )
                openai_error = "OpenAI health check failed"

        openai_response_time = (time.time() - openai_start) * 1000

        providers_status["openai"] = {
            "provider": "openai",
            "status": "healthy" if (openai_configured and openai_reachable) else "degraded" if openai_configured else "unavailable",
            "configured": openai_configured,
            "reachable": openai_reachable,
            "response_time": round(openai_response_time, 2),
            "last_check": datetime.datetime.now().isoformat(),
            "error": openai_error,
            "error_id": openai_error_id
        }
    except Exception as e:
        openai_error_id = generate_error_id()
        logger.error(
            "OpenAI health check failed [error_id=%s]", openai_error_id, exc_info=True,
        )
        providers_status["openai"] = {
            "provider": "openai",
            "status": "unavailable",
            "configured": bool(os.environ.get("OPENAI_API_KEY")),
            "reachable": False,
            "response_time": 0,
            "last_check": datetime.datetime.now().isoformat(),
            "error": "OpenAI health check failed",
            "error_id": openai_error_id
        }
    
    # Anthropic Health Check
    try:
        anthropic_start = time.time()
        anthropic_configured = bool(os.environ.get("ANTHROPIC_API_KEY"))
        anthropic_available = service.is_ai_available() and ProviderType.ANTHROPIC in service.get_available_providers()
        
        # Test actual connectivity if configured
        anthropic_reachable = False
        anthropic_error = None
        anthropic_error_id = None
        if anthropic_configured and anthropic_available:
            try:
                # Quick test with Anthropic API
                import anthropic
                client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
                # Use a minimal request to test connectivity
                response = client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=10,
                    messages=[{"role": "user", "content": "test"}]
                )
                anthropic_reachable = True
            except Exception as e:
                anthropic_error_id = generate_error_id()
                logger.error(
                    "Anthropic health-check connectivity test failed [error_id=%s]",
                    anthropic_error_id, exc_info=True,
                )
                anthropic_error = "Anthropic health check failed"

        anthropic_response_time = (time.time() - anthropic_start) * 1000

        providers_status["anthropic"] = {
            "provider": "anthropic",
            "status": "healthy" if (anthropic_configured and anthropic_reachable) else "degraded" if anthropic_configured else "unavailable",
            "configured": anthropic_configured,
            "reachable": anthropic_reachable,
            "response_time": round(anthropic_response_time, 2),
            "last_check": datetime.datetime.now().isoformat(),
            "error": anthropic_error,
            "error_id": anthropic_error_id
        }
    except Exception as e:
        anthropic_error_id = generate_error_id()
        logger.error(
            "Anthropic health check failed [error_id=%s]", anthropic_error_id, exc_info=True,
        )
        providers_status["anthropic"] = {
            "provider": "anthropic",
            "status": "unavailable",
            "configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "reachable": False,
            "response_time": 0,
            "last_check": datetime.datetime.now().isoformat(),
            "error": "Anthropic health check failed",
            "error_id": anthropic_error_id
        }
    
    # Overall status
    total_response_time = (time.time() - start_time) * 1000
    
    # Determine overall status
    healthy_count = sum(1 for p in providers_status.values() if p["status"] == "healthy")
    configured_count = sum(1 for p in providers_status.values() if p["configured"])
    
    overall_status = "healthy" if healthy_count > 0 else "degraded" if configured_count > 0 else "unavailable"
    
    return {
        "provider": "all",
        "status": overall_status,
        "providers": providers_status,
        "summary": {
            "healthy": healthy_count,
            "configured": configured_count,
            "total": len(providers_status)
        },
        "response_time": round(total_response_time, 2),
        "last_check": datetime.datetime.now().isoformat()
    }

@router.get("/ai/openai")
async def openai_health():
    """
    Check OpenAI API health status.
    This endpoint does not require authentication.
    """
    import time
    start_time = time.time()
    
    configured = bool(os.environ.get("OPENAI_API_KEY"))
    reachable = False
    error = None
    error_id = None

    if configured:
        try:
            import openai
            client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
            # Quick test with OpenAI API
            response = client.models.list()
            reachable = True
        except Exception as e:
            error_id = generate_error_id()
            logger.error("OpenAI health check failed [error_id=%s]", error_id, exc_info=True)
            error = "OpenAI health check failed"

    response_time = (time.time() - start_time) * 1000
    status = "healthy" if (configured and reachable) else "degraded" if configured else "unavailable"

    result = {
        "provider": "openai",
        "status": status,
        "configured": configured,
        "reachable": reachable,
        "response_time": round(response_time, 2),
        "last_check": datetime.datetime.now().isoformat()
    }

    if error:
        result["error"] = error
        result["error_id"] = error_id

    return result

@router.get("/ai/anthropic")
async def anthropic_health():
    """
    Check Anthropic API health status.
    This endpoint does not require authentication.
    """
    import time
    start_time = time.time()
    
    configured = bool(os.environ.get("ANTHROPIC_API_KEY"))
    reachable = False
    error = None
    error_id = None

    if configured:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
            # Quick test with Anthropic API
            response = client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=10,
                messages=[{"role": "user", "content": "test"}]
            )
            reachable = True
        except Exception as e:
            error_id = generate_error_id()
            logger.error("Anthropic health check failed [error_id=%s]", error_id, exc_info=True)
            error = "Anthropic health check failed"

    response_time = (time.time() - start_time) * 1000
    status = "healthy" if (configured and reachable) else "degraded" if configured else "unavailable"

    result = {
        "provider": "anthropic",
        "status": status,
        "configured": configured,
        "reachable": reachable,
        "response_time": round(response_time, 2),
        "last_check": datetime.datetime.now().isoformat()
    }

    if error:
        result["error"] = error
        result["error_id"] = error_id

    return result

# Container Health Check Endpoint
@router.get("/containers")
async def containers_health():
    """
    Check Docker container health status.
    This endpoint does not require authentication.
    """
    import subprocess
    import json
    import time
    from datetime import datetime, timedelta
    
    start_time = time.time()
    containers_status = {}
    overall_healthy = True
    error = None
    error_id = None

    try:
        # Get container status using docker command
        result = subprocess.run([
            'docker', 'ps', '--format', 
            '{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.CreatedAt}}\t{{.RunningFor}}'
        ], capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            
            for line in lines:
                if line.strip():
                    parts = line.split('\t')
                    if len(parts) >= 5:
                        name, status, image, created_at, running_for = parts
                        
                        # Determine if container is healthy
                        is_healthy = status.startswith('Up')
                        if not is_healthy:
                            overall_healthy = False
                        
                        # Parse uptime - handle various formats
                        uptime_seconds = 0
                        try:
                            if 'minute' in running_for:
                                minute_part = running_for.split('minute')[0].strip()
                                minutes_str = ''.join(filter(str.isdigit, minute_part))
                                if minutes_str:
                                    uptime_seconds = int(minutes_str) * 60
                            elif 'hour' in running_for:
                                hour_part = running_for.split('hour')[0].strip()
                                hours_str = ''.join(filter(str.isdigit, hour_part))
                                if hours_str:
                                    uptime_seconds = int(hours_str) * 3600
                            elif 'second' in running_for:
                                second_part = running_for.split('second')[0].strip()
                                seconds_str = ''.join(filter(str.isdigit, second_part))
                                if seconds_str:
                                    uptime_seconds = int(seconds_str)
                        except (ValueError, AttributeError):
                            uptime_seconds = 0
                        
                        containers_status[name] = {
                            "name": name,
                            "status": "healthy" if is_healthy else "unhealthy",
                            "docker_status": status,
                            "image": image,
                            "uptime_seconds": uptime_seconds,
                            "uptime_human": running_for,
                            "created_at": created_at,
                            "is_game_service": name.startswith('sectorwars2102')
                        }
        
        # Get more detailed stats for game containers
        for container_name in containers_status:
            if containers_status[container_name]["is_game_service"]:
                try:
                    # Get container stats
                    stats_result = subprocess.run([
                        'docker', 'stats', '--no-stream', '--format', 
                        '{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.BlockIO}}',
                        container_name
                    ], capture_output=True, text=True, timeout=5)
                    
                    if stats_result.returncode == 0:
                        stats_line = stats_result.stdout.strip()
                        if stats_line:
                            cpu, mem_usage, mem_perc, net_io, block_io = stats_line.split('\t')
                            containers_status[container_name].update({
                                "cpu_percent": cpu.replace('%', ''),
                                "memory_usage": mem_usage,
                                "memory_percent": mem_perc.replace('%', ''),
                                "network_io": net_io,
                                "block_io": block_io
                            })
                except Exception as e:
                    # Stats collection failed for this container, continue
                    logger.warning(f"Failed to collect stats for container {container_name}: {e}")
                    
    except subprocess.TimeoutExpired:
        error = "Docker command timed out"
        overall_healthy = False
    except FileNotFoundError:
        error = "Docker command not found"
        overall_healthy = False
    except Exception as e:
        error_id = generate_error_id()
        logger.error("Container health check failed [error_id=%s]", error_id, exc_info=True)
        error = "Container health check failed"
        overall_healthy = False
    
    response_time = (time.time() - start_time) * 1000
    
    # Count game containers
    game_containers = [c for c in containers_status.values() if c["is_game_service"]]
    healthy_game_containers = [c for c in game_containers if c["status"] == "healthy"]
    
    result = {
        "provider": "docker",
        "status": "healthy" if overall_healthy else "degraded",
        "containers": containers_status,
        "summary": {
            "total_containers": len(containers_status),
            "game_containers": len(game_containers),
            "healthy_game_containers": len(healthy_game_containers),
            "all_healthy": overall_healthy
        },
        "response_time": round(response_time, 2),
        "last_check": datetime.now().isoformat()
    }

    if error:
        result["error"] = error
        if error_id:
            result["error_id"] = error_id

    return result

# Database Health Check Endpoint
@router.get("/database")
async def database_health():
    """
    Check PostgreSQL database health status.
    This endpoint does not require authentication.
    """
    import time
    from sqlalchemy import text, inspect
    from src.core.database import engine
    from src.core.config import settings
    from urllib.parse import urlparse
    
    start_time = time.time()
    
    # Parse database URL for metadata
    db_url = settings.get_db_url()
    parsed = urlparse(db_url)
    
    connected = False
    pool_status = {}
    database_info = {}
    error = None
    error_id = None

    try:
        # Test database connection and gather metrics
        with engine.connect() as connection:
            connected = True
            
            # Get pool status
            pool = engine.pool
            pool_status = {
                "size": pool.size(),
                "checked_out": pool.checkedout(),
                "overflow": pool.overflow(),
                "total_connections": pool.checkedout() + pool.checkedin()
            }
            
            # Get database statistics
            # Database size in MB
            size_result = connection.execute(text(
                "SELECT pg_size_pretty(pg_database_size(current_database())) as size, "
                "pg_database_size(current_database()) / (1024 * 1024) as size_mb"
            )).fetchone()
            
            # Table count
            table_result = connection.execute(text(
                "SELECT COUNT(*) as table_count FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )).fetchone()
            
            # Active connections
            connections_result = connection.execute(text(
                "SELECT COUNT(*) as active_connections FROM pg_stat_activity "
                "WHERE state = 'active'"
            )).fetchone()
            
            database_info = {
                "size_mb": round(float(size_result.size_mb), 2),
                "size_pretty": size_result.size,
                "table_count": table_result.table_count,
                "active_connections": connections_result.active_connections
            }
            
    except Exception as e:
        error_id = generate_error_id()
        logger.error("Database health check failed [error_id=%s]", error_id, exc_info=True)
        error = "Database health check failed"
        connected = False
        # Set default values for failed connection
        pool_status = {
            "size": 0,
            "checked_out": 0,
            "overflow": 0,
            "total_connections": 0
        }
        database_info = {
            "size_mb": 0,
            "size_pretty": "Unknown",
            "table_count": 0,
            "active_connections": 0
        }
    
    response_time = (time.time() - start_time) * 1000
    status = "healthy" if connected else "unavailable"
    
    result = {
        "provider": "postgresql",
        "status": status,
        "host": parsed.hostname or "unknown",
        "database": parsed.path[1:] if parsed.path else "unknown",  # Remove leading '/'
        "connected": connected,
        "response_time": round(response_time, 2),
        "pool_status": pool_status,
        "database_info": database_info,
        "last_check": datetime.datetime.now().isoformat()
    }

    if error:
        result["error"] = error
        if error_id:
            result["error_id"] = error_id

    return result