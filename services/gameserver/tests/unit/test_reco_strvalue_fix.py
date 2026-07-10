"""Unit tests for WO-SWEEP-RECO-STRVALUE.

INCIDENT: the leg-7 re-drive of GET /ai/recommendations got past the
greenlet class (WO-SWEEP-RECO-GREENLET-2's exists-path fix held) and
surfaced a THIRD, independent mechanism: `'str' object has no attribute
'value'` (error_id 6a54f11d) in the recommendations assembly.

CONFIRMED MECHANISM (found by direct code inspection, then verified
empirically -- not the WO's own candidate sites, which turned out to be
clean on inspection; see the diagnosis report): `AIComprehensiveAssistant.
security_level` is a plain `String(20)` DB column
(src/models/enhanced_ai_models.py:80), NOT a native Postgres enum, with a
Python-side `SecurityLevel` enum DEFAULT applied only at construction. A
brand-new assistant (this request's own CREATE branch in
`_validate_and_authenticate`) still carries that enum instance in memory
pre-commit. Any EXISTING assistant loaded via the SELECT at
`_validate_and_authenticate` -- the common, repeat-visit case, and
therefore the SAME "profile row already exists" territory
WO-SWEEP-RECO-GREENLET-2 already established as the exists-path -- comes
back from SQLAlchemy as the raw column value: a plain `str` (e.g.
"standard"). Every one of the FIVE `_get_*_recommendations` methods
(trading, combat x2 branches, colony, station, strategic) assigns
`security_clearance_required=assistant.security_level` straight into a
`CrossSystemRecommendation` (typed `security_clearance_required:
SecurityLevel`) with zero normalization, and `CrossSystemRecommendation.
to_dict()` plus both `enhanced_ai.py` recommendation routes later call
`.value` on it unconditionally -- crashing the instant an EXISTING
assistant reaches any of those paths. `get_ai_performance_metrics` had
the identical bug on a direct `assistant.security_level.value` read
(silently swallowed by its own try/except into a degraded status
response rather than crashing outright, but the same defect class).

FIX: `_normalize_security_level()` (enhanced_ai_service.py, right after
`CrossSystemRecommendation`) coerces either shape to a real `SecurityLevel`
member, applied at all 7 read sites. This suite isolates the test to the
ONE thing the fix actually changes -- the `assistant.security_level` ->
`CrossSystemRecommendation.security_clearance_required` boundary -- via a
stubbed `trading_service` returning one correctly-typed
`TradingRecommendation` (real `RecommendationType`/`RiskLevel` enums,
matching every real `ai_trading_service.py` construction site, which were
independently confirmed clean during diagnosis), so a failure here can
only be attributed to the security_level boundary, not to
AITradingService's own internals (already covered by
test_reco_greenlet_fix.py / test_reco_greenlet_exists_path.py).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from src.models.enhanced_ai_models import SecurityLevel
from src.services.ai_trading_service import RecommendationType, RiskLevel, TradingRecommendation
from src.services.enhanced_ai_service import EnhancedAIService, _normalize_security_level


class _FakeAssistant:
    """Real attribute values, no ORM machinery needed. security_level is
    deliberately a PLAIN STRING here -- exactly what SQLAlchemy hands back
    for an EXISTING AIComprehensiveAssistant row (String(20) column, no
    Enum() coercion on read) -- the real failing shape from the live
    incident, not a synthetic one."""

    def __init__(self, security_level):
        self.id = uuid.uuid4()
        self.player_id = uuid.uuid4()
        self.security_level = security_level


class _FakeTradingService:
    """Stands in for AITradingService: returns one canned, correctly-typed
    TradingRecommendation (real RecommendationType/RiskLevel enums,
    matching ai_trading_service.py's own construction sites, all
    independently confirmed clean during diagnosis). Isolates this test to
    the ONE thing this WO fixes -- the security_level boundary -- not
    AITradingService's own internals."""

    async def get_trading_recommendations(self, db, player_id_str, max_count):
        return [
            TradingRecommendation(
                id=str(uuid.uuid4()),
                type=RecommendationType.BUY,
                commodity_id="organics",
                sector_id="5",
                target_price=100.0,
                expected_profit=500.0,
                confidence=0.8,
                risk_level=RiskLevel.LOW,
                reasoning="test recommendation",
                priority=3,
                expires_at=datetime.utcnow() + timedelta(hours=1),
            )
        ]


class _FakeDB:
    async def refresh(self, obj):  # noqa: ARG002 (WO-SWEEP-RECO-GREENLET-2's refresh call, no-op here)
        pass


def _make_service(security_level) -> EnhancedAIService:
    service = EnhancedAIService(_FakeDB())
    service.trading_service = _FakeTradingService()
    return service


class TestNormalizeSecurityLevel:
    def test_passes_through_a_real_enum_unchanged(self):
        result = _normalize_security_level(SecurityLevel.PREMIUM)
        assert result is SecurityLevel.PREMIUM

    def test_coerces_the_real_failing_shape_a_plain_str(self):
        """The exact live-incident shape: a plain str, as read back from
        the String(20) DB column for an EXISTING assistant row."""
        result = _normalize_security_level("standard")
        assert result is SecurityLevel.STANDARD
        assert result.value == "standard"


class TestGetTradingRecommendationsSecurityLevelBoundary:
    @pytest.mark.asyncio
    async def test_existing_assistant_str_security_level_does_not_crash(self):
        """Regression pin: before the fix, this raises AttributeError
        ('str' object has no attribute 'value') the moment the
        CrossSystemRecommendation is later serialized -- exactly the
        live-incident shape (an EXISTING assistant, str security_level)."""
        service = _make_service("standard")
        assistant = _FakeAssistant(security_level="standard")

        recommendations = await service._get_trading_recommendations(assistant, max_count=3)

        assert len(recommendations) == 1
        rec = recommendations[0]
        assert isinstance(rec.security_clearance_required, SecurityLevel), (
            f"expected a SecurityLevel enum, got {type(rec.security_clearance_required)!r} "
            f"(the live-incident shape: a bare str survived into the recommendation)"
        )
        assert rec.security_clearance_required == SecurityLevel.STANDARD

        # The actual crash site: to_dict() (and both enhanced_ai.py routes)
        # call .value unconditionally.
        serialized = rec.to_dict()
        assert serialized["security_clearance_required"] == "standard"

    @pytest.mark.asyncio
    async def test_fresh_assistant_enum_security_level_still_works(self):
        """The CREATE-branch shape (a real enum, never broken) must keep
        working identically -- this fix widens the accepted input, it
        doesn't change fresh-assistant behavior."""
        service = _make_service(SecurityLevel.ENTERPRISE)
        assistant = _FakeAssistant(security_level=SecurityLevel.ENTERPRISE)

        recommendations = await service._get_trading_recommendations(assistant, max_count=3)

        assert len(recommendations) == 1
        assert recommendations[0].security_clearance_required == SecurityLevel.ENTERPRISE
        assert recommendations[0].to_dict()["security_clearance_required"] == "enterprise"
