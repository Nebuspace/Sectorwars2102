"""Unit tests for WO-FIRSTLOGIN-CAT-BONUS-WIRING."""
from src.services.first_login_service import (
    CAT_MENTION_SCORE_BONUS,
    apply_cat_mention_score_bonus,
)


class TestApplyCatMentionScoreBonus:
    def test_no_mention_unchanged(self):
        score, applied = apply_cat_mention_score_bonus(0.50, ["I haul cargo regularly."])
        assert applied is False
        assert score == 0.50

    def test_cat_mention_adds_flat_bonus(self):
        score, applied = apply_cat_mention_score_bonus(
            0.50, ["Nice orange cat by the landing gear."]
        )
        assert applied is True
        assert score == 0.50 + CAT_MENTION_SCORE_BONUS

    def test_caps_at_one(self):
        score, applied = apply_cat_mention_score_bonus(0.95, ["I spotted the kitty."])
        assert applied is True
        assert score == 1.0

    def test_false_positive_category_ignored(self):
        score, applied = apply_cat_mention_score_bonus(
            0.40, ["Check the cargo category on the manifest."]
        )
        assert applied is False
        assert score == 0.40

    def test_any_exchange_triggers_once(self):
        score, applied = apply_cat_mention_score_bonus(
            0.40,
            ["Just passing through.", "Is that a tabby?", "Anyway, my papers."],
        )
        assert applied is True
        assert abs(score - 0.55) < 1e-9
