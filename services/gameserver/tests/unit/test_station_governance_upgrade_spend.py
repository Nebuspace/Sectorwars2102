"""LEG-2032: passed major-upgrade vote debits station treasury capex."""
import uuid
from datetime import datetime, UTC
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.models.port_ownership import StationGovernanceVote
from src.services.station_governance_service import (
    UPGRADE_VOTE_SPENT_KEY,
    _apply_passed_upgrade_spend,
)


def _vote_row(*, passed=True, capex=600_000, treasury=1_000_000):
    row = StationGovernanceVote(
        id=uuid.uuid4(),
        station_id=uuid.uuid4(),
        vote_type="upgrade",
        proposed_value={"capex": capex},
        status="passed" if passed else "failed",
        outcome={"passed": passed, "status": "passed" if passed else "failed"},
    )
    return row


def _station(treasury=1_000_000):
    station = MagicMock()
    station.id = uuid.uuid4()
    station.treasury_balance = treasury
    station.ownership = {}
    return station


@patch("src.services.station_governance_service.flag_modified")
@patch("src.services.station_governance_service._lock_station")
def test_passed_upgrade_debits_treasury_and_records_receipt(mock_lock, _flag):
    db = MagicMock()
    station = _station(treasury=1_000_000)
    mock_lock.return_value = station
    row = _vote_row(capex=600_000)
    now = datetime.now(UTC)

    _apply_passed_upgrade_spend(db, station, row, now)

    assert station.treasury_balance == 400_000
    spent = station.ownership[UPGRADE_VOTE_SPENT_KEY]
    assert len(spent) == 1
    assert spent[0]["capex"] == 600_000
    assert row.outcome["execution"]["success"] is True
    db.flush.assert_called()


@patch("src.services.station_governance_service._lock_station")
def test_insufficient_treasury_fails_closed(mock_lock):
    db = MagicMock()
    station = _station(treasury=100_000)
    mock_lock.return_value = station
    row = _vote_row(capex=600_000)

    _apply_passed_upgrade_spend(db, station, row, datetime.now(UTC))

    assert station.treasury_balance == 100_000
    assert row.outcome["execution"]["success"] is False
    assert row.outcome["execution"]["reason"] == "insufficient_treasury"


@patch("src.services.station_governance_service._lock_station")
def test_upgrade_spend_is_idempotent(mock_lock):
    db = MagicMock()
    station = _station(treasury=1_000_000)
    mock_lock.return_value = station
    row = _vote_row(capex=600_000)
    row.outcome["execution"] = {"action": "debit_capex", "success": True, "capex": 600_000}

    _apply_passed_upgrade_spend(db, station, row, datetime.now(UTC))

    assert station.treasury_balance == 1_000_000
    db.flush.assert_not_called()
