"""Policy proposal validation (WO-REGOV-CITIZEN-API).

Validates a policy's ``proposed_changes`` payload AT PROPOSAL TIME so an
invalid or unknown key is rejected with a 400 before a RegionalPolicy row is
ever written — the canon "Validator catches at proposal time (400)"
(sw2102-docs SYSTEMS/regional-governance.md:189). The known-keys/bounds
mirror ``RegionalGovernanceService.enact_changes_onto_region`` (HEAD
regional_governance_service.py:160-238), the function that actually APPLIES a
PASSED policy onto the region row: that function CLAMPS out-of-range values
and silently skips unknown keys (enactment must never fail an already-passed
vote). This validator is stricter — it REJECTS both, so a citizen never has a
proposal accepted only to have half of it silently dropped weeks later at
enactment.

Deliberately out of scope: ``POLICY_TREASURY_KEY`` ("treasury_adjustment",
regional_governance_service.py:270) is a separate reserved key consumed by
``compute_treasury_adjustment``/``finalize_policy``, not by
``enact_changes_onto_region`` — no current canon policy type carries it, so
it is NOT in the known-keys set below (a proposal naming it is rejected as
unknown, matching the mirrored scope this validator was asked to enforce).
"""

from typing import Any, Dict, List

from src.models.region import GovernanceType

# Mirrors the CHECK-bounded columns enact_changes_onto_region clamps into
# (region.py CHECK constraints + regional_governance_service.py:160-238).
TAX_RATE_MIN, TAX_RATE_MAX = 0.05, 0.25
VOTING_THRESHOLD_MIN, VOTING_THRESHOLD_MAX = 0.1, 0.9
ELECTION_FREQUENCY_DAYS_MIN, ELECTION_FREQUENCY_DAYS_MAX = 30, 365
GOVERNANCE_QUORUM_PCT_MIN, GOVERNANCE_QUORUM_PCT_MAX = 0.25, 0.60
TRADE_BONUS_MIN, TRADE_BONUS_MAX = 1.0, 3.0

# trade_bonuses key reserved for the ADR-0062 tariff (not a multiplier —
# enact_changes_onto_region skips it too; mirrored here so a policy proposing
# a tariff_rate change isn't rejected as out-of-band for the multiplier band).
_TRADE_BONUS_RESERVED_KEYS = frozenset({"tariff_rate"})

KNOWN_POLICY_KEYS = frozenset({
    "tax_rate",
    "voting_threshold",
    "election_frequency_days",
    "governance_type",
    "governance_quorum_pct",
    "trade_bonuses",
})


def _validate_float_range(value: Any, field: str, lo: float, hi: float) -> List[str]:
    """Parse ``value`` as a float and check it falls in [lo, hi]; returns a
    single-element (or empty) error list."""
    try:
        fvalue = float(value)
    except (TypeError, ValueError):
        return [f"{field} must be a number"]
    if not (lo <= fvalue <= hi):
        return [f"{field} must be between {lo} and {hi} (got {fvalue})"]
    return []


def validate_proposed_changes(proposed_changes: Dict[str, Any]) -> List[str]:
    """Return a list of validation error strings (empty = valid).

    Every top-level key must be one of KNOWN_POLICY_KEYS; every present value
    must parse to the right type and fall within its CHECK-bounded range (or
    be a valid GovernanceType value / an in-band trade_bonuses map). Called at
    proposal time by BOTH the member and owner POST /policies routes — a
    non-empty return means the route must reject with 400 before writing a
    row."""
    errors: List[str] = []
    changes = proposed_changes or {}

    for key in changes:
        if key not in KNOWN_POLICY_KEYS:
            errors.append(f"unknown proposed_changes key: {key!r}")

    if "tax_rate" in changes:
        errors.extend(_validate_float_range(
            changes["tax_rate"], "tax_rate", TAX_RATE_MIN, TAX_RATE_MAX
        ))

    if "voting_threshold" in changes:
        errors.extend(_validate_float_range(
            changes["voting_threshold"], "voting_threshold",
            VOTING_THRESHOLD_MIN, VOTING_THRESHOLD_MAX,
        ))

    if "election_frequency_days" in changes:
        value = changes["election_frequency_days"]
        try:
            ivalue = int(value)
            if not (ELECTION_FREQUENCY_DAYS_MIN <= ivalue <= ELECTION_FREQUENCY_DAYS_MAX):
                errors.append(
                    f"election_frequency_days must be between "
                    f"{ELECTION_FREQUENCY_DAYS_MIN} and {ELECTION_FREQUENCY_DAYS_MAX} "
                    f"(got {ivalue})"
                )
        except (TypeError, ValueError):
            errors.append("election_frequency_days must be an integer")

    if "governance_type" in changes:
        value = changes["governance_type"]
        valid = {g.value for g in GovernanceType}
        if str(value) not in valid:
            errors.append(f"governance_type must be one of {sorted(valid)} (got {value!r})")

    if "governance_quorum_pct" in changes:
        errors.extend(_validate_float_range(
            changes["governance_quorum_pct"], "governance_quorum_pct",
            GOVERNANCE_QUORUM_PCT_MIN, GOVERNANCE_QUORUM_PCT_MAX,
        ))

    if "trade_bonuses" in changes:
        tb = changes["trade_bonuses"]
        if not isinstance(tb, dict):
            errors.append("trade_bonuses must be an object mapping resource -> bonus")
        else:
            for resource, bonus in tb.items():
                if resource in _TRADE_BONUS_RESERVED_KEYS:
                    continue
                errors.extend(_validate_float_range(
                    bonus, f"trade_bonuses[{resource!r}]",
                    TRADE_BONUS_MIN, TRADE_BONUS_MAX,
                ))

    return errors
