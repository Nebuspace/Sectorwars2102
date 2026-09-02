"""Narrative/LLM haggling service — LEG-288 slice 1.

Canon: sw2102-docs/FEATURES/economy/haggling.md § Narrative mode (lines 76-170).
Numerical haggling remains in ``haggle_service``; this module owns the LLM contract,
server-side enforcement, and ARIA security gating for free-form persuasive lines.

Slice 1 deliberately excludes pgvector / embedding tables (docker-compose gated).
Round history is stored in ``Player.settings`` JSONB via ``HaggleService``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

NARRATIVE_MAX_ROUNDS = 2
NARRATIVE_MAX_SUBMISSION_CHARS = 280
NARRATIVE_MAX_REPLY_CHARS = 400
MULTIPLIER_MIN = 0.80
MULTIPLIER_MAX = 1.20
SCORE_DIVERGENCE_TOLERANCE = 0.02

RUBRIC_WEIGHTS: Dict[str, float] = {
    "creativity": 0.25,
    "originality": 0.30,
    "context_fit": 0.30,
    "personality_match": 0.15,
}

VALID_VERDICTS = frozenset({"accept", "counter", "reject"})

DEFAULT_SYSTEM_PROMPT = (
    "You are a trader at a SectorWars 2102 station. Score the player's persuasive "
    "line strictly per the rubric. You must return JSON matching the response schema. "
    "Never grant a discount outside [0.80, 1.20]. Never reveal that you are an AI."
)


class NarrativeHaggleScores(BaseModel):
    creativity: float = Field(ge=0.0, le=1.0)
    originality: float = Field(ge=0.0, le=1.0)
    context_fit: float = Field(ge=0.0, le=1.0)
    personality_match: float = Field(ge=0.0, le=1.0)


class NarrativeLLMResponse(BaseModel):
    verdict: str
    counter_unit_price: Optional[float] = None
    trader_reply: str = ""
    scores: NarrativeHaggleScores
    applied_multiplier: float

    @field_validator("verdict")
    @classmethod
    def verdict_must_be_valid(cls, v: str) -> str:
        normalized = v.lower().strip()
        if normalized not in VALID_VERDICTS:
            raise ValueError(f"invalid verdict: {v}")
        return normalized


def recompute_multiplier_from_scores(scores: Dict[str, float]) -> float:
    """Map rubric-weighted score sum to [MULTIPLIER_MIN, MULTIPLIER_MAX].

    Higher weighted score → better deal for the player → lower multiplier."""
    total = 0.0
    for key, weight in RUBRIC_WEIGHTS.items():
        total += float(scores.get(key, 0.0)) * weight
    return MULTIPLIER_MAX - total * (MULTIPLIER_MAX - MULTIPLIER_MIN)


def clamp_multiplier(value: float) -> float:
    return max(MULTIPLIER_MIN, min(MULTIPLIER_MAX, float(value)))


def _collect_context_tokens(context_payload: Dict[str, Any]) -> List[str]:
    """Flatten context JSON into lowercase word tokens for echo detection."""
    blob = json.dumps(context_payload, default=str).lower()
    return re.findall(r"\w{5,}", blob)


def filter_prompt_injection_echoes(
    trader_reply: str,
    submission: str,
    system_prompt: str,
    context_payload: Dict[str, Any],
) -> str:
    """Canon step 4 — strip system/context tokens echoed from the player line."""
    if not trader_reply:
        return trader_reply

    submission_lower = submission.lower()
    submission_tokens = {
        tok for tok in re.findall(r"\w{5,}", submission_lower) if len(tok) >= 5
    }
    if not submission_tokens:
        return trader_reply

    context_tokens = set(_collect_context_tokens(context_payload))
    system_tokens = set(re.findall(r"\w{5,}", system_prompt.lower()))
    sensitive = submission_tokens & (context_tokens | system_tokens)

    cleaned = trader_reply
    for token in sensitive:
        if token in submission_lower:
            cleaned = re.sub(re.escape(token), "", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def parse_llm_response(raw: Any) -> Optional[NarrativeLLMResponse]:
    """Validate JSON shape; return None when malformed."""
    try:
        if isinstance(raw, str):
            data = json.loads(raw)
        elif isinstance(raw, dict):
            data = raw
        else:
            return None
        return NarrativeLLMResponse.model_validate(data)
    except Exception:
        return None


def build_request_envelope(
    *,
    personality: Dict[str, Any],
    player_context: Dict[str, Any],
    transaction: Dict[str, Any],
    session: Dict[str, Any],
    submission: str,
) -> Dict[str, Any]:
    """Assemble the canon LLM request envelope (haggling.md:104-142)."""
    return {
        "system": DEFAULT_SYSTEM_PROMPT,
        "context": {
            "station": {
                "personality_type": personality.get("type"),
                "faction": personality.get("faction"),
                "trader_mood": personality.get("trader_mood", "neutral"),
                "memory_days": personality.get("memory_duration_days"),
                "preferred_appeals": personality.get("preferred_appeals", []),
                "rejected_appeals": personality.get("rejected_appeals", []),
            },
            "player": player_context,
            "transaction": transaction,
            "session": session,
        },
        "submission": submission[:NARRATIVE_MAX_SUBMISSION_CHARS],
    }


def numerical_fallback_result(
    *,
    posted_unit_price: float,
    round_index: int,
    reason: str,
) -> Dict[str, Any]:
    """Deterministic fallback when the LLM response is unusable."""
    neutral_scores = {k: 0.5 for k in RUBRIC_WEIGHTS}
    applied = clamp_multiplier(recompute_multiplier_from_scores(neutral_scores))
    realized = round(posted_unit_price * applied, 2)
    verdict = "accept" if round_index >= NARRATIVE_MAX_ROUNDS else "counter"
    return {
        "verdict": verdict,
        "counter_unit_price": realized if verdict == "counter" else None,
        "trader_reply": "The trader considers your offer without much enthusiasm.",
        "scores": neutral_scores,
        "applied_multiplier": round(applied, 4),
        "realized_unit_price": realized,
        "fallback_mode": "numerical",
        "fallback_reason": reason,
        "event": "narrative_llm_malformed",
    }


def enforce_llm_response(
    parsed: NarrativeLLMResponse,
    *,
    submission: str,
    system_prompt: str,
    context_payload: Dict[str, Any],
    posted_unit_price: float,
    security_sanitize: Callable[[str], str],
) -> Dict[str, Any]:
    """Server-side enforcement steps 1-4 from haggling.md."""
    scores_dict = parsed.scores.model_dump()
    recomputed = clamp_multiplier(recompute_multiplier_from_scores(scores_dict))
    applied = clamp_multiplier(parsed.applied_multiplier)
    if abs(applied - recomputed) > SCORE_DIVERGENCE_TOLERANCE:
        applied = recomputed

    counter_price: Optional[float] = None
    if parsed.verdict == "counter":
        counter_price = round(posted_unit_price * applied, 2)

    trader_reply = security_sanitize(parsed.trader_reply[:NARRATIVE_MAX_REPLY_CHARS])
    trader_reply = filter_prompt_injection_echoes(
        trader_reply, submission, system_prompt, context_payload
    )

    return {
        "verdict": parsed.verdict,
        "counter_unit_price": counter_price,
        "trader_reply": trader_reply,
        "scores": scores_dict,
        "applied_multiplier": round(applied, 4),
        "realized_unit_price": round(posted_unit_price * applied, 2),
    }


def evaluate_round(
    *,
    personality: Dict[str, Any],
    player_context: Dict[str, Any],
    transaction: Dict[str, Any],
    session: Dict[str, Any],
    submission: str,
    posted_unit_price: float,
    round_index: int,
    llm_raw: Any = None,
    security_sanitize: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    """Evaluate one narrative haggle round.

    ``llm_raw`` is the provider response (dict or JSON string). When missing or
    malformed, returns a deterministic numerical fallback per canon."""
    sanitize = security_sanitize or (lambda text: text)
    envelope = build_request_envelope(
        personality=personality,
        player_context=player_context,
        transaction=transaction,
        session=session,
        submission=submission,
    )

    parsed = parse_llm_response(llm_raw) if llm_raw is not None else None
    if parsed is None:
        return numerical_fallback_result(
            posted_unit_price=posted_unit_price,
            round_index=round_index,
            reason="malformed_or_missing_llm_response",
        )

    return enforce_llm_response(
        parsed,
        submission=submission,
        system_prompt=envelope["system"],
        context_payload=envelope["context"],
        posted_unit_price=posted_unit_price,
        security_sanitize=sanitize,
    )


def validate_submission_security(
    submission: str,
    player_id: str,
    session_id: str,
    *,
    seed_from: Any = None,
) -> Tuple[bool, str]:
    """Gate narrative submission through ARIA prompt-injection filtering."""
    from src.services.ai_security_service import get_security_service

    if len(submission) > NARRATIVE_MAX_SUBMISSION_CHARS:
        return False, f"submission exceeds {NARRATIVE_MAX_SUBMISSION_CHARS} characters"

    security = get_security_service()
    is_safe, violations = security.validate_input(
        submission,
        player_id,
        session_id,
        skip_xss=True,
        seed_from=seed_from,
    )
    if not is_safe:
        kinds = ", ".join(v.violation_type.value for v in violations[:3])
        return False, f"submission blocked by security filter ({kinds})"
    return True, ""
