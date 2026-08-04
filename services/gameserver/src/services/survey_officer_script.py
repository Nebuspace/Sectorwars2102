"""Scripted (non-LLM) Survey Officer dialogue for onboarding (ADR-0091 M43).

Explicit AI-safety carve-out: v1 is scripted, deterministic template text
only — no ARIA/LLM call. Per the ADR (line ~157): "ARIA-voiced only post-v1
behind AI-safety sign-off." Do not wire an LLM call into this module.
"""

from typing import Dict, List

SURVEY_OFFICER_NAME = "Survey Officer Reyes"

# Ordered scripted lines introducing the ground-expedition mechanic during
# onboarding. Plain template strings — `{planet_name}` is the only
# substitution point, filled by the caller.
SURVEY_OFFICER_INTRO_LINES: List[str] = [
    (
        "Survey Officer {survey_officer_name}: \"Welcome aboard. Before you settle, "
        "I run a ground expedition — a quick scan of the surface to size up the site: "
        "usable slots, hazards, what's worth digging up.\""
    ),
    (
        "Survey Officer {survey_officer_name}: \"Your first expedition on {planet_name} "
        "is on the house — no cost, and I've made sure it comes back a good one so you "
        "get a real feel for the mechanic.\""
    ),
    (
        "Survey Officer {survey_officer_name}: \"I'll also run a couple of free demo "
        "scans so you can see how a re-roll works before it ever costs you anything.\""
    ),
    (
        "Survey Officer {survey_officer_name}: \"This site is reserved for you while "
        "you look it over — nobody else can settle here out from under you.\""
    ),
]


def render_survey_officer_intro(planet_name: str) -> List[str]:
    """Format the scripted intro lines for a given starter planet name."""
    return [
        line.format(
            survey_officer_name=SURVEY_OFFICER_NAME,
            planet_name=planet_name,
        )
        for line in SURVEY_OFFICER_INTRO_LINES
    ]


def get_survey_officer_intro_payload(planet_name: str) -> Dict[str, object]:
    """Small dict shape convenient for a route/response to attach as-is."""
    return {
        "speaker": SURVEY_OFFICER_NAME,
        "scripted": True,
        "lines": render_survey_officer_intro(planet_name),
    }
