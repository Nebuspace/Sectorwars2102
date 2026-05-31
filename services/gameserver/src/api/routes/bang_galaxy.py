"""Admin endpoints for the ``sw2102-bang`` galaxy generator.

Five endpoints (per Phase 1D of ``DOCS/PLANS/bang-integration.md``):

* ``POST /admin/galaxy/jobs``           — queue a generation job (202 + job_id)
* ``POST /admin/galaxy/preview``        — preview seed inline (no job row)
* ``GET  /admin/galaxy/jobs/{job_id}``  — full job status / warnings / log
* ``GET  /admin/galaxy/jobs/{job_id}/stream`` — SSE tail of ``log_text``
* ``DELETE /admin/galaxy/{galaxy_id}``  — hard-delete galaxy (requires header)

All endpoints are admin-only via :func:`get_current_admin`. The legacy
``POST /admin/galaxy/generate`` (in :mod:`src.api.routes.admin`) is kept
intact; Phase 4 removes it.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Response,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from src.auth.dependencies import get_current_admin
from src.core.database import get_async_session
from src.models.bang_generation_job import (
    BangGenerationJob,
    BangGenerationJobStatus,
)
from src.models.galaxy import Galaxy
from src.models.user import User
from src.schemas.bang_config import BangConfig
from src.schemas.bang_job import (
    BangJobCreate,
    BangJobListItem,
    BangJobListResponse,
    BangJobResponse,
)
from src.services.bang_import_service import BangImportService

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Dependency: BangImportService factory
# ---------------------------------------------------------------------------


def get_bang_import_service() -> BangImportService:
    """Return a fresh translator instance per request.

    Stateless — safe to share — but instantiated per request so tests can
    override via :meth:`FastAPI.dependency_overrides`.
    """
    return BangImportService()


# ---------------------------------------------------------------------------
# POST /admin/galaxy/jobs
# ---------------------------------------------------------------------------


@router.post(
    "/galaxy/jobs",
    response_model=BangJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_bang_job(
    payload: BangJobCreate,
    background_tasks: BackgroundTasks,
    current_admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_async_session),
    service: BangImportService = Depends(get_bang_import_service),
) -> BangJobResponse:
    """Queue a bang generation job. Returns immediately with the job row."""
    job = BangGenerationJob(
        admin_user_id=current_admin.id,
        status=BangGenerationJobStatus.PENDING,
        params_json=payload.config.model_dump(),
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    region_metadata: Dict[str, Any] = {
        "galaxy_name": payload.galaxy_name or "SectorWars Galaxy",
        "master_seed": payload.config.seed,
    }
    # SQLAlchemy returns Column-typed values on instance access for mypy; the
    # runtime value is a uuid.UUID, so cast for the BackgroundTasks signature.
    job_id: uuid.UUID = job.id  # type: ignore[assignment]
    background_tasks.add_task(
        service.run_generation_job,
        job_id,
        payload.config,
        region_metadata=region_metadata,
    )
    return BangJobResponse.model_validate(job)


# ---------------------------------------------------------------------------
# POST /admin/galaxy/preview
# ---------------------------------------------------------------------------


@router.post("/galaxy/preview")
async def preview_bang_config(
    config: BangConfig,
    current_admin: User = Depends(get_current_admin),
    service: BangImportService = Depends(get_bang_import_service),
) -> Dict[str, Any]:
    """Run bang with ``--validate-only`` and return stats inline."""
    report = await asyncio.to_thread(service.validate_only, config)
    return {
        "stats": report.stats,
        "warnings": report.warnings,
        "validation": report.validation,
    }


# ---------------------------------------------------------------------------
# GET /admin/galaxy/jobs   (paginated history list — small payload)
# ---------------------------------------------------------------------------


@router.get("/galaxy/jobs", response_model=BangJobListResponse)
async def list_bang_jobs(
    page: int = 0,
    page_size: int = 20,
    current_admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_async_session),
) -> BangJobListResponse:
    """Paginated job history. Excludes `log_text` to keep payloads small."""
    from sqlalchemy import desc, func, select

    page = max(0, page)
    page_size = max(1, min(200, page_size))

    total = (await session.execute(select(func.count(BangGenerationJob.id)))).scalar_one()

    rows = (await session.execute(
        select(BangGenerationJob)
        .order_by(desc(BangGenerationJob.started_at))
        .offset(page * page_size)
        .limit(page_size)
    )).scalars().all()

    items: list[BangJobListItem] = []
    for r in rows:
        item = BangJobListItem.model_validate(r)
        # warning_count is derived; not a column on the ORM model.
        item = item.model_copy(update={"warning_count": len(r.warnings_json or [])})
        items.append(item)

    return BangJobListResponse(items=items, total=int(total), page=page, page_size=page_size)


# ---------------------------------------------------------------------------
# GET /admin/galaxy/jobs/{job_id}
# ---------------------------------------------------------------------------


@router.get("/galaxy/jobs/{job_id}", response_model=BangJobResponse)
async def get_bang_job(
    job_id: uuid.UUID,
    current_admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_async_session),
) -> BangJobResponse:
    job = await session.get(BangGenerationJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return BangJobResponse.model_validate(job)


# ---------------------------------------------------------------------------
# GET /admin/galaxy/jobs/{job_id}/stream  (SSE)
# ---------------------------------------------------------------------------


@router.get("/galaxy/jobs/{job_id}/stream")
async def stream_bang_job_log(
    job_id: uuid.UUID,
    current_admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_async_session),
) -> StreamingResponse:
    """Server-Sent-Events stream of new lines appended to ``log_text``.

    Polls the row every 250 ms and emits any text delta. Closes when the
    job leaves the ``RUNNING`` state.
    """
    job = await session.get(BangGenerationJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator() -> AsyncGenerator[bytes, None]:
        last_seen = 0
        # New session per stream to avoid holding the request's session open.
        from src.core.database import AsyncSessionLocal  # noqa: WPS433
        while True:
            async with AsyncSessionLocal() as stream_session:
                row = await stream_session.get(BangGenerationJob, job_id)
                if row is None:
                    break
                log = row.log_text or ""
                if len(log) > last_seen:
                    delta = log[last_seen:]
                    last_seen = len(log)
                    for line in delta.splitlines(keepends=False):
                        yield f"data: {line}\n\n".encode("utf-8")
                if row.status != BangGenerationJobStatus.RUNNING:
                    final_status = row.status.value if hasattr(row.status, "value") else str(row.status)
                    yield f"event: status\ndata: {final_status}\n\n".encode("utf-8")
                    break
            await asyncio.sleep(0.25)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# DELETE /admin/galaxy/{galaxy_id}
# ---------------------------------------------------------------------------


@router.delete(
    "/galaxy/{galaxy_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def hard_delete_galaxy(
    galaxy_id: uuid.UUID,
    current_admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_async_session),
    x_confirm_galaxy_name: Optional[str] = Header(
        default=None,
        alias="X-Confirm-Galaxy-Name",
        description="Must match the galaxy's exact name to authorise deletion.",
    ),
) -> Response:
    """Hard-delete a galaxy (and everything cascaded). Per Max, no archive."""
    galaxy = await session.get(Galaxy, galaxy_id)
    if galaxy is None:
        raise HTTPException(status_code=404, detail="Galaxy not found")
    if x_confirm_galaxy_name is None or x_confirm_galaxy_name != galaxy.name:
        raise HTTPException(
            status_code=422,
            detail=(
                "X-Confirm-Galaxy-Name header missing or does not match galaxy "
                "name; deletion refused."
            ),
        )
    await session.delete(galaxy)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# GET /admin/bang/version   (server-side BANG_VERSION exposure)
# ---------------------------------------------------------------------------


@router.get("/bang/version")
async def get_bang_version(
    current_admin: User = Depends(get_current_admin),
) -> Dict[str, str]:
    """Return the BANG_VERSION the server is pinned to.

    Used by the admin UI's overview header to warn when the active galaxy
    was generated under a different bang version than the server is
    currently configured to invoke.
    """
    import os
    return {
        "bang_version": os.environ.get("BANG_VERSION", "unknown"),
        "default_image": os.environ.get(
            "BANG_IMAGE",
            f"docker.io/drxelanull/sw2102-bang:{os.environ.get('BANG_VERSION', '1.3.0')}",
        ),
    }


__all__ = ["router"]
