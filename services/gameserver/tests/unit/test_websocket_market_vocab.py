"""Unit coverage for the public market-stream WebSocket's commodity
vocabulary fix (WO-ARCH-RES-2H-RUNTIME-VOCAB).

Pure Python + a mocked Redis client — no live server, no live Redis. Proves:
  * ?commodities= parses to canon lowercase slugs, 'ALL' expands to the full
    canon set, unknown/UPPER_CASE input is dropped;
  * the subscribe-side channel string ("market:{slug}") built from the parsed
    commodity list is byte-identical to the publish-side channel string
    every real market-data writer publishes to — the previous UPPER_CASE
    subscription list could never match a lowercase publish (the public
    stream was dead as wired).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.api.routes.enhanced_websocket import _parse_market_stream_commodities
from src.core.commodity_economy import COMMODITY_BASE_PRICES
from src.services.redis_pubsub_service import RedisPubSubService


@pytest.mark.unit
class TestParseMarketStreamCommodities:
    def test_all_expands_to_full_canon_set(self):
        assert _parse_market_stream_commodities("ALL") == list(COMMODITY_BASE_PRICES)

    def test_comma_separated_list_lowercased(self):
        assert _parse_market_stream_commodities("ore,fuel") == ["ore", "fuel"]

    def test_mixed_case_input_is_normalized(self):
        assert _parse_market_stream_commodities("Ore, FUEL") == ["ore", "fuel"]

    def test_unknown_slug_is_filtered_out(self):
        assert _parse_market_stream_commodities("PLASMA") == []

    def test_previously_missing_canon_commodities_now_subscribable(self):
        """gourmet_food/exotic_technology/colonists/precious_metals were
        absent from the old UPPER_CASE valid list entirely."""
        result = _parse_market_stream_commodities(
            "gourmet_food,exotic_technology,colonists,precious_metals"
        )
        assert result == ["gourmet_food", "exotic_technology", "colonists", "precious_metals"]

    def test_stale_non_canon_slugs_are_gone(self):
        """'LUXURY'/'TECHNOLOGY' were never real commodity slugs (the real
        ones are 'luxury_goods'/'equipment')."""
        assert _parse_market_stream_commodities("LUXURY,TECHNOLOGY") == []


@pytest.mark.unit
class TestChannelCasingMatchesPublisher:
    def test_subscribe_channels_for_ore_and_fuel(self):
        commodity_list = _parse_market_stream_commodities("ore,fuel")
        channels = [f"market:{commodity}" for commodity in commodity_list]
        assert channels == ["market:ore", "market:fuel"]

    @pytest.mark.asyncio
    async def test_published_channel_matches_a_subscribed_channel(self):
        """End-to-end channel-casing proof: the channel a subscriber joins
        for 'ore' is exactly the channel publish_market_update publishes to
        when called with the same (always-lowercase) commodity slug."""
        pubsub_service = RedisPubSubService()
        pubsub_service.redis_client = AsyncMock()
        pubsub_service.redis_client.publish = AsyncMock(return_value=1)

        commodity_list = _parse_market_stream_commodities("ore,fuel")
        subscribe_channels = [f"market:{c}" for c in commodity_list]

        await pubsub_service.publish_market_update("ore", {"current_price": 30.0})

        published_channel = pubsub_service.redis_client.publish.call_args.args[0]
        assert published_channel == "market:ore"
        assert published_channel in subscribe_channels

    @pytest.mark.asyncio
    async def test_uppercase_subscription_would_never_have_matched(self):
        """Regression pin for the fixed bug: the OLD subscribe channel for
        'ORE' never equals the publish channel, which is always lowercase."""
        pubsub_service = RedisPubSubService()
        pubsub_service.redis_client = AsyncMock()
        pubsub_service.redis_client.publish = AsyncMock(return_value=1)

        stale_subscribe_channel = f"market:{'ORE'}"

        await pubsub_service.publish_market_update("ore", {"current_price": 30.0})

        published_channel = pubsub_service.redis_client.publish.call_args.args[0]
        assert published_channel != stale_subscribe_channel
