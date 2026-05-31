"""Unit tests for the BangGenerationJob model + Pydantic schemas.

DB tests (round-trip, FK) use the existing ``db`` fixture from
``conftest.py``. Pure schema tests have no DB dependency.
"""
import json
import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from src.models.bang_generation_job import (
    BangGenerationJob,
    BangGenerationJobStatus,
)
from src.models.user import User
from src.schemas.bang_config import BangConfig
from src.schemas.bang_job import (
    BangJobCreate,
    BangJobResponse,
    BangJobStatus,
    BangJobWarning,
)

# ---------------------------------------------------------------------------
# Pure schema tests (no DB)
# ---------------------------------------------------------------------------


def test_bang_config_minimal_valid() -> None:
    cfg = BangConfig(seed=42, sectors=1000, region_type="player_owned")
    assert cfg.seed == 42
    assert cfg.sectors == 1000
    assert cfg.region_type == "player_owned"
    # All optional fields default to None.
    assert cfg.port_percent is None
    assert cfg.validator_strictness is None


def test_bang_config_rejects_oversized_sectors() -> None:
    with pytest.raises(ValidationError):
        BangConfig(seed=1, sectors=99_999, region_type="player_owned")


def test_bang_config_rejects_unknown_region_type() -> None:
    with pytest.raises(ValidationError):
        BangConfig(seed=1, sectors=100, region_type="unknown")  # type: ignore[arg-type]


def test_bang_config_is_frozen() -> None:
    cfg = BangConfig(seed=1, sectors=100, region_type="central_nexus")
    # frozen=True triggers ValidationError on attribute assignment.
    with pytest.raises(ValidationError):
        cfg.seed = 99  # type: ignore[misc]


def test_bang_config_round_trip_via_json() -> None:
    cfg = BangConfig(
        seed=2_147_483_648,  # > int32 to exercise BIGINT-ish range
        sectors=5000,
        region_type="central_nexus",
        federation_percent=20.0,
        port_percent=12.5,
        validator_strictness="strict",
    )
    raw = cfg.model_dump_json()
    restored = BangConfig.model_validate_json(raw)
    assert restored == cfg


def test_bang_job_create_wraps_config() -> None:
    payload = BangJobCreate(
        config=BangConfig(seed=1, sectors=200, region_type="player_owned"),
        galaxy_name="Test Galaxy",
    )
    assert payload.config.seed == 1
    assert payload.galaxy_name == "Test Galaxy"


def test_bang_job_warning_allows_extra_fields() -> None:
    # extra='allow' lets bang attach arbitrary diagnostic data.
    w = BangJobWarning(
        category="TOPOLOGY_RESCUE",
        code="B-040",
        message="rescued island",
        data={"sector": 142},
        custom_key="ok",  # type: ignore[call-arg]
    )
    assert w.code == "B-040"


def test_job_status_enum_values() -> None:
    # The status enum is the contract between the model and the API.
    assert {s.value for s in BangGenerationJobStatus} == {
        "PENDING",
        "RUNNING",
        "COMPLETE",
        "FAILED",
    }


# ---------------------------------------------------------------------------
# DB round-trip tests (use the rollback-per-test `db` fixture from conftest)
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_user(db: Session) -> User:
    user = User(
        id=uuid.uuid4(),
        username=f"bang-admin-{uuid.uuid4().hex[:8]}",
        email=f"bang-{uuid.uuid4().hex[:8]}@test.local",
        is_admin=True,
    )
    db.add(user)
    db.flush()
    return user


def test_create_and_round_trip(db: Session, admin_user: User) -> None:
    cfg = BangConfig(seed=42, sectors=500, region_type="player_owned")
    job = BangGenerationJob(
        id=uuid.uuid4(),
        admin_user_id=admin_user.id,
        status=BangGenerationJobStatus.PENDING,
        params_json=json.loads(cfg.model_dump_json()),
    )
    db.add(job)
    db.flush()

    refetched = db.query(BangGenerationJob).filter_by(id=job.id).first()
    assert refetched is not None
    assert refetched.status == BangGenerationJobStatus.PENDING
    assert refetched.params_json["seed"] == 42
    assert refetched.params_json["region_type"] == "player_owned"
    assert refetched.warnings_json == []
    assert refetched.log_text == ""
    assert refetched.started_at is not None


def test_status_transition(db: Session, admin_user: User) -> None:
    job = BangGenerationJob(
        admin_user_id=admin_user.id,
        status=BangGenerationJobStatus.PENDING,
        params_json={"seed": 1, "sectors": 100, "region_type": "player_owned"},
    )
    db.add(job)
    db.flush()

    job.status = BangGenerationJobStatus.RUNNING
    db.flush()
    assert job.status == BangGenerationJobStatus.RUNNING

    job.status = BangGenerationJobStatus.COMPLETE
    job.completed_at = datetime.now(timezone.utc)
    job.duration_ms = 12_345
    db.flush()

    refetched = db.query(BangGenerationJob).filter_by(id=job.id).first()
    assert refetched is not None
    assert refetched.status == BangGenerationJobStatus.COMPLETE
    assert refetched.duration_ms == 12_345


def test_serialize_to_response_schema(db: Session, admin_user: User) -> None:
    """The Pydantic response schema reads cleanly from the SQLAlchemy row."""
    job = BangGenerationJob(
        admin_user_id=admin_user.id,
        status=BangGenerationJobStatus.COMPLETE,
        params_json={"seed": 7, "sectors": 200, "region_type": "terran_space"},
        warnings_json=[
            {
                "category": "EMISSION_UNDERTARGET",
                "code": "B-014",
                "message": "ore emission 92% of target",
            }
        ],
        log_text="ok\n",
        duration_ms=999,
    )
    db.add(job)
    db.flush()

    resp = BangJobResponse.model_validate(job)
    assert resp.status == "COMPLETE"
    assert resp.duration_ms == 999
    assert len(resp.warnings_json) == 1
    assert resp.warnings_json[0].code == "B-014"

    status_view = BangJobStatus.model_validate(job)
    assert status_view.status == "COMPLETE"
