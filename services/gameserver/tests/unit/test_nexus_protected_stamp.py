"""WO-BUILD-NEXUS-IS-PROTECTED-CLUSTER-STAMP

Pins the Phase 12.5b Gateway Plaza / Capital stamp predicate used by
bang_import_service when materialising central_nexus sectors.
"""
from src.services.bang_import_service import (
    NEXUS_GATEWAY_PLAZA_SECTOR_HI,
    NEXUS_GATEWAY_PLAZA_SECTOR_LO,
    _should_stamp_nexus_protected,
)


def test_gateway_plaza_range_constants():
    assert NEXUS_GATEWAY_PLAZA_SECTOR_LO == 2251
    assert NEXUS_GATEWAY_PLAZA_SECTOR_HI == 2500


def test_stamp_capital_and_plaza_bounds():
    assert _should_stamp_nexus_protected("central_nexus", 2251) is True
    assert _should_stamp_nexus_protected("central_nexus", 2500) is True
    assert _should_stamp_nexus_protected("central_nexus", 2375) is True


def test_stamp_excludes_outside_plaza_and_other_regions():
    assert _should_stamp_nexus_protected("central_nexus", 2250) is False
    assert _should_stamp_nexus_protected("central_nexus", 2501) is False
    assert _should_stamp_nexus_protected("central_nexus", 1) is False
    assert _should_stamp_nexus_protected("terran_space", 2251) is False
    assert _should_stamp_nexus_protected("player_owned", 2251) is False
