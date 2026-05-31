"""Pydantic schemas for the BangGenerationJob API surface.

These shapes are returned from `POST /api/admin/galaxy/jobs` (create),
`GET /api/admin/galaxy/jobs/{id}` (status), and the history listing. The
SSE log endpoint streams plain text, not these models.

See `src/models/bang_generation_job.py` for the persisted shape and
`DOCS/PLANS/bang-integration.md` § "Phase 1D" for the endpoint contracts.
"""
from datetime import datetime
from typing import Any, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.bang_config import BangConfig

JobStatusLiteral = Literal["PENDING", "RUNNING", "COMPLETE", "FAILED"]


class BangJobCreate(BaseModel):
    """Payload for `POST /api/admin/galaxy/jobs`.

    Wraps a `BangConfig` so the same shape can be re-issued from the
    history table's "Regenerate with same seed" button.
    """

    model_config = ConfigDict(extra="forbid")

    config: BangConfig
    # Operator-supplied galaxy name; if absent the orchestrator derives one.
    galaxy_name: Optional[str] = Field(default=None, max_length=100)


class BangJobWarning(BaseModel):
    """A single categorized warning surfaced by bang or the translator."""

    model_config = ConfigDict(extra="allow")

    category: str = Field(..., description="e.g., TOPOLOGY_RESCUE, EMISSION_UNDERTARGET")
    code: str = Field(..., description="Stable identifier, e.g., B-040")
    message: str
    data: Optional[dict] = None


class BangJobStatus(BaseModel):
    """Minimal status snapshot. Polled by the admin UI."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: JobStatusLiteral
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    warning_count: int = 0


class BangJobResponse(BaseModel):
    """Full job record returned from the detail endpoint and history table."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    admin_user_id: UUID
    status: JobStatusLiteral
    params_json: dict[str, Any]
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    warnings_json: List[BangJobWarning] = Field(default_factory=list)
    # `log_text` may be large; clients fetching the full record get it inline.
    # The SSE stream endpoint is the preferred channel for in-flight logs.
    log_text: str = ""


class BangJobListItem(BaseModel):
    """Row shape for the history list — no log_text to keep responses small."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    admin_user_id: UUID
    status: JobStatusLiteral
    params_json: dict[str, Any]
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    warning_count: int = 0


class BangJobListResponse(BaseModel):
    """Paginated job-history listing for the admin UI."""

    model_config = ConfigDict(extra="forbid")

    items: List[BangJobListItem]
    total: int
    page: int = Field(ge=0)
    page_size: int = Field(ge=1, le=200)
