"""WO-ARIA-COST-CAPS -- converges ai_security_service.py's cost engine on
canon (OPERATIONS/aria.md + SYSTEMS/aria-dialogue.md, both amended
2026-07-10 per Max's GO on ADR-0092 §4) and enforces it on both chat paths
BEFORE any real LLM spend exists.

No DB needed: AISecurityService is a pure in-memory singleton (dict-backed
cost/rate tracking) -- every test constructs its OWN fresh instance to
avoid cross-test pollution of the real module-level singleton.
"""
from __future__ import annotations

from dataclasses import fields
from typing import Any, Dict, List, Tuple

import pytest

from src.services.ai_security_service import (
    AISecurityService,
    PlayerSecurityProfile,
)


@pytest.mark.unit
class TestCanonDefaults:
    def test_five_values_match_canon(self) -> None:
        svc = AISecurityService()
        assert svc.rate_limits["requests_per_minute"] == 10
        assert svc.rate_limits["requests_per_day"] == 500
        assert svc.rate_limits["max_cost_per_day_usd"] == 2.00
        assert svc.rate_limits["max_cost_per_request"] == 0.25
        assert svc.rate_limits["instance_max_cost_per_day_usd"] == 50.00

    def test_no_per_hour_key_remains(self) -> None:
        """Absence pin -- requests_per_hour is RETIRED (dominated by the
        per-minute cap; canon: 10 req/min reaches the old 100/hr ceiling
        in 6 minutes, zero enforcement value)."""
        svc = AISecurityService()
        assert "requests_per_hour" not in svc.rate_limits

    def test_profile_dataclass_has_no_hourly_counter(self) -> None:
        field_names = {f.name for f in fields(PlayerSecurityProfile)}
        assert "request_count_1hour" not in field_names
        assert "request_count_1min" in field_names  # still present
        assert "request_count_1day" in field_names  # still present

    def test_env_overrides_respected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARIA_DAILY_USD", "5.00")
        monkeypatch.setenv("ARIA_REQ_USD", "0.99")
        monkeypatch.setenv("ARIA_INSTANCE_DAILY_USD", "100.00")
        monkeypatch.setenv("ARIA_RPM", "20")
        svc = AISecurityService()
        assert svc.rate_limits["max_cost_per_day_usd"] == 5.00
        assert svc.rate_limits["max_cost_per_request"] == 0.99
        assert svc.rate_limits["instance_max_cost_per_day_usd"] == 100.00
        assert svc.rate_limits["requests_per_minute"] == 20


@pytest.mark.unit
class TestPerRequestCap:
    def test_oversized_single_request_rejected(self) -> None:
        svc = AISecurityService()
        result = svc.check_cost_limits_detailed("player-1", 0.30)
        assert result.allowed is False
        assert result.reason == "request_cap"
        assert result.error_code == "ERR_REQUEST_COST_CAP_EXCEEDED"
        assert result.scope == "personal"

    def test_exactly_at_ceiling_is_allowed(self) -> None:
        """Hard ceiling is `>`, not `>=` -- exactly $0.25 is not "exceeding" it."""
        svc = AISecurityService()
        result = svc.check_cost_limits_detailed("player-1", 0.25)
        assert result.allowed is True

    def test_estimate_ai_cost_no_longer_silently_capped_at_stale_value(self) -> None:
        """The old `min(estimated_cost, 0.05)` pinned every estimate at the
        PRE-amendment per-request figure, making the new $0.25 check
        structurally unreachable from a real estimate. A large-enough
        input must now produce an estimate that genuinely exceeds the old
        stale $0.05 ceiling."""
        svc = AISecurityService()
        huge_message = "x" * 200_000
        estimate = svc.estimate_ai_cost(huge_message, model="gpt-4")
        assert estimate > 0.05


@pytest.mark.unit
class TestDailyCapEightyPercentReserve:
    def test_blocks_once_current_spend_reaches_80_percent(self) -> None:
        svc = AISecurityService()
        svc.track_cost("player-1", 1.60)  # exactly 80% of the $2.00 default
        result = svc.check_cost_limits_detailed("player-1", 0.01)
        assert result.allowed is False
        assert result.reason == "daily_cap"
        assert result.error_code == "ERR_DAILY_BUDGET_EXHAUSTED"
        assert result.scope == "personal"

    def test_allows_just_under_80_percent(self) -> None:
        svc = AISecurityService()
        svc.track_cost("player-1", 1.59)
        result = svc.check_cost_limits_detailed("player-1", 0.01)
        assert result.allowed is True

    def test_gate_is_on_current_spend_not_a_projection(self) -> None:
        """The block fires because CURRENT spend already crossed 80%, not
        because (current + this request) would cross it -- a request that
        by itself wouldn't tip the balance is still blocked once the
        reserve line is already crossed."""
        svc = AISecurityService()
        svc.track_cost("player-1", 1.60)
        result = svc.check_cost_limits_detailed("player-1", 0.001)  # tiny
        assert result.allowed is False

    def test_other_players_unaffected(self) -> None:
        svc = AISecurityService()
        svc.track_cost("player-1", 1.60)
        result = svc.check_cost_limits_detailed("player-2", 0.01)
        assert result.allowed is True


@pytest.mark.unit
class TestInstanceCircuitBreaker:
    def test_trips_on_aggregate_spend_across_two_different_players(self) -> None:
        svc = AISecurityService()
        svc.track_cost("player-A", 30.00)
        svc.track_cost("player-B", 20.00)  # aggregate now exactly $50.00

        # player-C has spent NOTHING personally -- still rejected, because
        # the instance gate is upstream of the per-player gate (canon).
        result = svc.check_cost_limits_detailed("player-C", 0.01)
        assert result.allowed is False
        assert result.reason == "instance_breaker"
        assert result.error_code == "ERR_INSTANCE_COST_CAP_EXCEEDED"
        assert result.scope == "instance"

    def test_upstream_of_per_player_gate_even_for_a_player_under_their_own_cap(self) -> None:
        svc = AISecurityService()
        svc.track_cost("player-A", 50.00)
        # player-B has $0 personal spend (nowhere near their own 80%
        # reserve line) -- the instance breaker still rejects them, and
        # the reported reason/scope is the INSTANCE one, not "daily_cap".
        result = svc.check_cost_limits_detailed("player-B", 0.01)
        assert result.allowed is False
        assert result.reason == "instance_breaker"
        assert result.scope == "instance"

    def test_below_the_instance_ceiling_does_not_trip(self) -> None:
        svc = AISecurityService()
        svc.track_cost("player-A", 49.99)
        result = svc.check_cost_limits_detailed("player-B", 0.01)
        assert result.allowed is True

    def test_track_cost_increments_both_player_and_instance_ledgers(self) -> None:
        svc = AISecurityService()
        svc.track_cost("player-1", 0.10)
        svc.track_cost("player-1", 0.05)
        today_key = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d")
        assert svc.cost_tracking[f"player-1:{today_key}"] == pytest.approx(0.15)
        assert svc.instance_cost_tracking[today_key] == pytest.approx(0.15)


@pytest.mark.unit
class TestBackwardCompatibleBooleanForm:
    def test_check_cost_limits_still_returns_a_plain_bool(self) -> None:
        """first_login.py's existing `if not security_service.check_cost_
        limits(...)` call site is unchanged -- this must keep returning a
        plain bool, not the richer CostLimitResult."""
        svc = AISecurityService()
        assert svc.check_cost_limits("player-1", 0.01) is True
        svc.track_cost("player-1", 1.60)
        assert svc.check_cost_limits("player-1", 0.01) is False

    def test_first_login_transparently_gains_instance_breaker_protection(self) -> None:
        """The ONE existing live spend path (first_login.py's answer_
        dialogue) never learned about CostLimitResult -- it keeps calling
        the same bool method, and now ALSO gets rejected by an instance-
        wide trip it previously had zero protection against."""
        svc = AISecurityService()
        svc.track_cost("someone-else", 50.00)
        assert svc.check_cost_limits("first-login-player", 0.01) is False


@pytest.mark.unit
class TestRateLimitsStillEnforced:
    def test_per_minute_cap_still_works(self) -> None:
        svc = AISecurityService()
        for _ in range(svc.rate_limits["requests_per_minute"]):
            svc.update_request_tracking("player-1")
        assert svc.check_rate_limits("player-1") is False

    def test_per_day_cap_still_works(self) -> None:
        svc = AISecurityService()
        profile = svc.get_or_create_player_profile("player-1")
        profile.request_count_1day = svc.rate_limits["requests_per_day"]
        from datetime import datetime
        profile.last_request_time = datetime.utcnow()
        assert svc.check_rate_limits("player-1") is False


# --- chat-path fallback: correct scope flag both ways, never a hard error - #

class _FakeAsyncDbNoPlayerRow:
    """WO-ARIA-TRUST-PERSIST: chat_with_ai now fetches a Player row via
    `db.get(...)` to seed/write-through the trust ladder, even on a
    cost-blocked request. This fake simulates "no row found" (a real,
    harmless path -- the route's write-through block no-ops when
    player_row is None) without needing a real AsyncSession."""

    async def get(self, model: Any, pk: Any) -> None:
        return None

    async def commit(self) -> None:  # pragma: no cover -- unreached when get() returns None
        pass


class _FakeAsyncSessionLocalNoPlayerRow:
    """WO-ARIA-TRUST-PERSIST: handle_aria_chat now opens its OWN short-
    lived `AsyncSessionLocal()` to fetch/seed/write-through the trust
    ladder, before this WO a real DB call these tests never needed. Fakes
    the async-context-manager protocol; `.get()` returns None (harmless
    no-row-found path -- same reasoning as _FakeAsyncDbNoPlayerRow)."""

    async def __aenter__(self) -> "_FakeAsyncSessionLocalNoPlayerRow":
        return self

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False

    async def get(self, model: Any, pk: Any) -> None:
        return None

    async def commit(self) -> None:  # pragma: no cover -- unreached when get() returns None
        pass


@pytest.mark.unit
class TestChatPathFallback:
    def test_enhanced_ai_chat_degrades_on_personal_cap_hit(self) -> None:
        import asyncio

        from src.api.routes.enhanced_ai import ConversationRequest, chat_with_ai

        svc = AISecurityService()
        player_id = "11111111-1111-1111-1111-111111111111"
        svc.track_cost(player_id, 1.60)  # already at the 80% reserve line

        request = ConversationRequest(message="What's a good trade route?")
        result = asyncio.run(
            chat_with_ai(request=request, player_id=player_id, db=_FakeAsyncDbNoPlayerRow(), security_service=svc)
        )

        assert result.degraded is True
        assert result.scope == "personal"
        # Plain operational notice -- explicitly NOT in-character flavor
        # text (narration is a later ARIA WO's job per dispatch).
        assert "quantum storm" not in result.response.lower()
        assert "attunement" not in result.response.lower()

    def test_enhanced_ai_chat_degrades_on_instance_breaker_scope_instance(self) -> None:
        import asyncio

        from src.api.routes.enhanced_ai import ConversationRequest, chat_with_ai

        svc = AISecurityService()
        svc.track_cost("some-other-player", 50.00)  # trips the instance breaker

        fresh_player_id = "22222222-2222-2222-2222-222222222222"  # $0 personal spend
        request = ConversationRequest(message="Any tips for combat?")
        result = asyncio.run(
            chat_with_ai(request=request, player_id=fresh_player_id, db=_FakeAsyncDbNoPlayerRow(), security_service=svc)
        )

        assert result.degraded is True
        assert result.scope == "instance"

    def test_ws_aria_chat_sends_fallback_with_scope_personal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import asyncio

        import src.services.ai_security_service as ai_security_module
        import src.services.websocket_service as ws_module

        svc = AISecurityService()
        player_id = "33333333-3333-3333-3333-333333333333"
        svc.track_cost(player_id, 1.60)
        monkeypatch.setattr(ai_security_module, "get_security_service", lambda: svc)
        import src.core.database as database_module
        monkeypatch.setattr(database_module, "AsyncSessionLocal", _FakeAsyncSessionLocalNoPlayerRow)

        ws_module.connection_manager.connection_metadata["user-1"] = {
            "user_data": {"player_id": player_id},
        }
        sent: List[Tuple[str, Dict[str, Any]]] = []

        async def _fake_send(user_id: str, message: Dict[str, Any]) -> bool:
            sent.append((user_id, message))
            return True

        monkeypatch.setattr(ws_module.connection_manager, "send_personal_message", _fake_send)

        asyncio.run(
            ws_module.handle_aria_chat("user-1", {"content": "hello ARIA"})
        )

        assert len(sent) == 1
        _, message = sent[0]
        assert message["type"] == "aria_response"
        assert message["data"]["degraded"] is True
        assert message["data"]["scope"] == "personal"

    def test_ws_aria_chat_sends_fallback_with_scope_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import asyncio

        import src.services.ai_security_service as ai_security_module
        import src.services.websocket_service as ws_module

        svc = AISecurityService()
        svc.track_cost("some-other-player", 50.00)
        monkeypatch.setattr(ai_security_module, "get_security_service", lambda: svc)
        import src.core.database as database_module
        monkeypatch.setattr(database_module, "AsyncSessionLocal", _FakeAsyncSessionLocalNoPlayerRow)

        fresh_player_id = "44444444-4444-4444-4444-444444444444"
        ws_module.connection_manager.connection_metadata["user-2"] = {
            "user_data": {"player_id": fresh_player_id},
        }
        sent: List[Tuple[str, Dict[str, Any]]] = []

        async def _fake_send(user_id: str, message: Dict[str, Any]) -> bool:
            sent.append((user_id, message))
            return True

        monkeypatch.setattr(ws_module.connection_manager, "send_personal_message", _fake_send)

        asyncio.run(
            ws_module.handle_aria_chat("user-2", {"content": "hello ARIA"})
        )

        assert len(sent) == 1
        _, message = sent[0]
        assert message["data"]["degraded"] is True
        assert message["data"]["scope"] == "instance"
