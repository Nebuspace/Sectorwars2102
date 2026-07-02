"""Verify-first evidence for WO-INTEGRITY-PAIR NH5 ("ranking auto-pass above
Captain").

The WO's premise: ranks missing from ``RANK_REQUIREMENTS`` AUTO-PASS
``check_rank_requirements`` -> free promotion / credit-farming, to be gated
fail-closed "unless canon says otherwise".

Verify-first finding: canon DOES say otherwise, explicitly and in two places
that agree with each other and with the code:

  * ``ranking_service.py`` itself, directly above the dict: "Ranks not listed
    have no achievement requirements beyond points" (src/services/
    ranking_service.py:56).
  * sw2102-docs/FEATURES/gameplay/ranking.md "Achievement gates beyond
    points" table lists exactly the same 8 ranks (Sergeant..Captain) and no
    others — Senior Captain and every Flag-tier rank are deliberately
    points-only, by design, not an oversight.

So the "missing entries auto-pass" behavior is NOT the described exploit —
it is documented, intentional design on both sides of the code/canon line
(neither diverges from the other). Per the WO's own escape hatch ("fail-
closed unless canon says otherwise"), no fail-closed gate was added: doing
so would silently deviate FROM published canon and would block every
player's already-designed path to Senior Captain/Flag ranks, which is exactly
the kind of unilateral canon deviation the project's constitution forbids.
This file exists as the falsifiable evidence for that call, and as a
regression guard: if RANK_REQUIREMENTS or the canon-gated-rank set drifts,
this test breaks and forces a conscious decision instead of a silent one.

NH5 is being escalated back to the orchestrator as a likely false positive on
the audit's part (or a "does canon need to change" design question) rather
than closed by a code change here.
"""
import pytest

from src.services.ranking_service import RankingService, RANK_DEFINITIONS, RANK_REQUIREMENTS

# The exact 8 ranks + thresholds documented in sw2102-docs/FEATURES/gameplay/
# ranking.md "Achievement gates beyond points" (mirrors RANK_REQUIREMENTS 1:1).
CANON_GATED_RANKS = {
    "Sergeant": {"min_trades": 25, "min_sectors_visited": 50},
    "Staff Sergeant": {"min_trades": 50, "min_combat_victories": 10},
    "Master Sergeant": {"min_trades": 100, "min_combat_victories": 25, "min_sectors_visited": 100},
    "Warrant Officer": {"min_combat_victories": 50, "min_trades": 200},
    "Ensign": {"min_combat_victories": 100, "min_planets_owned": 1},
    "Lieutenant": {"min_trades": 500, "min_combat_victories": 200},
    "Commander": {"min_planets_owned": 3, "min_combat_victories": 500},
    "Captain": {"min_trades": 1000, "min_combat_victories": 1000, "min_planets_owned": 5},
}

ALL_RANK_NAMES = [r["name"] for r in RANK_DEFINITIONS]
UNGATED_RANK_NAMES = [n for n in ALL_RANK_NAMES if n not in CANON_GATED_RANKS]


def test_rank_requirements_matches_published_canon_exactly():
    """No silent drift between code and the canon table this finding rests on."""
    assert RANK_REQUIREMENTS == CANON_GATED_RANKS


@pytest.mark.parametrize("rank_name", UNGATED_RANK_NAMES)
def test_ungated_ranks_auto_pass_by_documented_design(rank_name):
    """Recruit/Spacer/Corporal (entry) and Senior Captain..Fleet Admiral
    (points-only Flag/Officer top end) all auto-pass — exactly as both the
    in-code comment and canon's table say they should. This is the verified
    "already matches canon, not a bug" outcome for NH5's premise."""
    service = RankingService(db=None)  # auto-pass path never touches self.db
    result = service.check_rank_requirements(player_id="unused", target_rank=rank_name)
    assert result["met"] is True
    assert result["requirements"] == {}
    assert result["missing"] == []


@pytest.mark.parametrize("rank_name,expected", CANON_GATED_RANKS.items())
def test_gated_ranks_still_require_documented_achievements(rank_name, expected):
    """The 8 canon-gated ranks are untouched by this investigation — still
    genuinely gated, confirming NH5's fix is not needed anywhere in the
    table."""
    assert RANK_REQUIREMENTS[rank_name] == expected
