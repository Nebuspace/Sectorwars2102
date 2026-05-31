"""Bang galaxy generation job model.

Tracks asynchronous galaxy-generation jobs invoked from the admin UI. Each
row represents one attempt to generate a galaxy via the `sw2102-bang` CLI
sidecar (see DOCS/PLANS/bang-integration.md). The row owns the job's
lifecycle (status, timing, warnings, error message, log output) and is the
source of truth for orphan-recovery sweeps at startup.

See `BangGenerationJob` for column-level documentation.
"""
import enum
import uuid

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.core.database import Base


class BangGenerationJobStatus(enum.Enum):
    """Lifecycle states for a bang generation job."""

    PENDING = "PENDING"      # Queued; advisory lock not yet acquired
    RUNNING = "RUNNING"      # Bang subprocesses executing / translator writing
    COMPLETE = "COMPLETE"    # Transaction committed; Galaxy is READY
    FAILED = "FAILED"        # Aborted (bang error, translator error, or orphaned)


class BangGenerationJob(Base):
    """A single invocation of the bang galaxy generator.

    Persists request parameters, status, warnings, and the full text log of
    the subprocess stderr stream. On worker crash mid-run, the startup hook
    in `src.main` flips RUNNING rows older than 5 minutes to FAILED with
    error_message="orphaned at startup".
    """

    __tablename__ = "bang_generation_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status = Column(
        Enum(BangGenerationJobStatus, name="bang_generation_job_status"),
        nullable=False,
        default=BangGenerationJobStatus.PENDING,
    )
    # Request params (BangConfig serialized) so the job can be re-run with the
    # same inputs from the history table.
    params_json = Column(JSONB, nullable=False)
    started_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)
    # Categorized warnings emitted by bang or the translator's Phase-13
    # validators. Shape: list of {category, code, message, data?}.
    warnings_json = Column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    duration_ms = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    # Raw stderr stream from all 3 bang subprocesses + translator progress.
    # SSE endpoint tails this column live.
    log_text = Column(Text, nullable=False, default="", server_default="")

    # Reverse relationship intentionally not declared on User: this is an
    # admin-only audit table; we don't expose it from the user object graph.

    __table_args__ = (
        # Orphan-recovery query: `WHERE status='RUNNING' AND started_at < ...`.
        Index(
            "ix_bang_generation_jobs_status_started_at",
            "status",
            "started_at",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<BangGenerationJob {self.id} status={self.status.name} "
            f"started_at={self.started_at}>"
        )
