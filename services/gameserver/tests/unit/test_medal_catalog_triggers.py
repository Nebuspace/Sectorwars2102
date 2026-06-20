"""Unit tests for medal-catalog trigger-type integrity (WO-CG3).

The four former ``special_discovery`` medals (orange_cat_society, honorary_tabby,
pioneer_office_pillar, ghost_in_the_static) shared one trigger_type at thresholds
1/2/3/4, so a single count-based ``_evaluate_and_award("special_discovery", N)``
would cascade-award every medal with ``threshold <= N`` (over-award). WO-CG3 splits
the collision: each medal now has its OWN trigger_type. These tests lock that fix in
place and assert the group can no longer be swept.

Pure-catalog tests — no DB, no fixtures: import the catalog and inspect it.
"""

import pytest

from src.services.medal_catalog import MEDAL_CATALOG, medals_for_trigger


# The four medals and their NEW distinct trigger_types (WO-CG3).
SPECIAL_MEDAL_TRIGGERS = {
    "special.orange_cat_society": "cat_mention_first_login",
    "special.honorary_tabby": "honorary_tabby_combo",
    "special.pioneer_office_pillar": "pioneer_office_civic",
    "special.ghost_in_the_static": "dark_territory_discovery",
}


def test_special_discovery_collision_is_gone():
    """No medal may keep the legacy shared ``special_discovery`` trigger_type."""
    assert medals_for_trigger("special_discovery") == [], (
        "The special_discovery trigger group must be empty after WO-CG3 — any "
        "member would re-introduce the cascade over-award bug."
    )


@pytest.mark.parametrize("medal_id,expected_trigger", SPECIAL_MEDAL_TRIGGERS.items())
def test_each_special_medal_has_its_distinct_trigger(medal_id, expected_trigger):
    """Each former special_discovery medal carries its own distinct trigger_type."""
    entry = MEDAL_CATALOG[medal_id]
    assert entry["criteria"]["type"] == expected_trigger


@pytest.mark.parametrize("medal_id,trigger", SPECIAL_MEDAL_TRIGGERS.items())
def test_each_special_trigger_owns_exactly_one_medal(medal_id, trigger):
    """A distinct trigger_type must select EXACTLY its one medal — never a group.

    This is the property that makes ``_evaluate_and_award`` un-sweepable for these
    medals: with one member per trigger, a count-based call can award at most that
    single medal, regardless of the count value.
    """
    matches = medals_for_trigger(trigger)
    assert len(matches) == 1, (
        f"trigger {trigger!r} should own exactly one medal, got "
        f"{[m['id'] for m in matches]}"
    )
    assert matches[0]["id"] == medal_id


@pytest.mark.parametrize("medal_id,trigger", SPECIAL_MEDAL_TRIGGERS.items())
def test_distinct_triggers_are_unique_across_catalog(medal_id, trigger):
    """The four new trigger_types must not collide with ANY other catalog trigger."""
    owners = [
        mid
        for mid, entry in MEDAL_CATALOG.items()
        if entry["criteria"].get("type") == trigger
    ]
    assert owners == [medal_id]


def test_special_medal_effects_and_legacy_keys_unchanged():
    """WO-CG3 changed only trigger_type — effects and legacy_keys must be preserved."""
    # Effects (the load-bearing gameplay identity) are unchanged by the split.
    cat = MEDAL_CATALOG["special.orange_cat_society"]
    assert cat["criteria"]["legacy_key"] == "orange_cat_society"
    assert cat["criteria"]["icon"] == "cat_orange"
    assert cat["effect"]["kind"] == "special"  # exempt-from-cap haggle lever
    assert cat["effect"]["magnitude"] == pytest.approx(0.15)

    tabby = MEDAL_CATALOG["special.honorary_tabby"]
    assert tabby["criteria"]["legacy_key"] == "honorary_tabby"
    assert tabby["effect"]["magnitude"] == pytest.approx(0.02)

    pillar = MEDAL_CATALOG["special.pioneer_office_pillar"]
    assert pillar["criteria"]["legacy_key"] == "pioneer_office_pillar"
    assert pillar["effect"]["magnitude"] == pytest.approx(2.0)

    ghost = MEDAL_CATALOG["special.ghost_in_the_static"]
    assert ghost["criteria"]["legacy_key"] == "ghost_in_the_static"
    assert ghost["effect"] is None  # pure prestige


def test_wired_special_dispatcher_exists_and_is_module_level():
    """The first-login special dispatcher exists with the expected signature shape."""
    import inspect
    from src.services import medal_service

    fn = getattr(medal_service, "check_and_award_first_login_special_medals", None)
    assert fn is not None, "WO-CG3 dispatcher must be defined"
    params = list(inspect.signature(fn).parameters)
    # (db, session_id)
    assert params == ["db", "session_id"]
