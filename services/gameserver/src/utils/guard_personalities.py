"""
Guard Personality System for First Login

Generates deterministic guard personalities based on session ID. This is
the sole source of guard identity — WO-PUX-FLOGIN-RESUME retired the
client-side hash mirror (guardPersonalities.ts); the frontend now reads the
persisted guard_* columns off the session/status responses instead of
re-deriving them.
"""

from dataclasses import dataclass
from typing import List


@dataclass
class GuardTrait:
    name: str
    base_suspicion: float
    description: str


@dataclass
class GuardPersonality:
    name: str
    title: str
    trait: str
    base_suspicion: float
    description: str


# Must match frontend exactly
GUARD_FIRST_NAMES = [
    'Chen', 'Rodriguez', 'Sato', "O'Brien", 'Kowalski', 'Singh',
    'Müller', 'Nakamura', 'Garcia', 'Petrov', 'Kim', 'Anderson'
]

GUARD_TITLES = [
    'Security Officer',
    'Guard',
    'Security Chief',
    'Station Inspector',
    'Docking Authority',
    'Customs Officer'
]

GUARD_TRAITS = [
    GuardTrait(
        name='Strict Rule-Follower',
        base_suspicion=0.6,
        description="By-the-book enforcer who trusts procedure over instinct"
    ),
    GuardTrait(
        name='Friendly Veteran',
        base_suspicion=0.3,
        description="Experienced officer who's seen it all and can spot a good story"
    ),
    GuardTrait(
        name='Paranoid Newbie',
        base_suspicion=0.7,
        description="Fresh recruit trying to prove themselves, suspicious of everyone"
    ),
    GuardTrait(
        name='Tired Night-Shifter',
        base_suspicion=0.4,
        description="Exhausted from long shifts, just wants to process paperwork quickly"
    ),
    GuardTrait(
        name='Shrewd Investigator',
        base_suspicion=0.5,
        description="Keen observer who listens carefully and catches inconsistencies"
    ),
    GuardTrait(
        name='Cynical Bureaucrat',
        base_suspicion=0.55,
        description="Seen too many lies to trust anyone easily"
    )
]


def get_guard_for_session(session_id: str) -> GuardPersonality:
    """
    Get a consistent guard personality for a session.
    Uses session ID as seed for deterministic randomness.

    Called once at session creation (first_login_service.py's
    get_or_create_session) and persisted onto the FirstLoginSession row;
    the frontend never re-derives this, it just reads the persisted columns.
    """
    # Simple hash function to convert session ID to number
    hash_value = 0
    for char in session_id:
        hash_value = ((hash_value << 5) - hash_value) + ord(char)
        hash_value = hash_value & 0xFFFFFFFF  # Convert to 32-bit integer

    # Convert to signed 32-bit if necessary
    if hash_value >= 0x80000000:
        hash_value -= 0x100000000

    # Use hash to seed selections (matching frontend bit shifting)
    name_index = abs(hash_value) % len(GUARD_FIRST_NAMES)
    title_index = abs(hash_value >> 4) % len(GUARD_TITLES)
    trait_index = abs(hash_value >> 8) % len(GUARD_TRAITS)

    first_name = GUARD_FIRST_NAMES[name_index]
    title = GUARD_TITLES[title_index]
    trait = GUARD_TRAITS[trait_index]

    return GuardPersonality(
        name=first_name,
        title=title,
        trait=trait.name,
        base_suspicion=trait.base_suspicion,
        description=trait.description
    )
