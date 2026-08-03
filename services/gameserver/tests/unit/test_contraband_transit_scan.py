"""WO-K2 — the black-market brief's SECOND detection site: a customs scan when a
jump carries contraband into a higher-security sector.

The brief always specified two detection sites — "on sell into, or TRANSIT OUT
OF, a higher-security sector" (``audit/design-briefs/black-market.md``:18) and
"detection also invocable on sector egress from ``services/movement_service.py``"
(:47). Only the sell site was built; ``ContrabandService.scan_in_transit`` is the
other one, wired into all three of ``move_player_to_sector``'s success branches.

What is actually at risk in that change, and therefore what this file pins:

* **The polarity flip.** The transit model reuses every ``DETECT_*`` weight
  verbatim and changes exactly one thing — the sector term becomes
  ``destination_security / 10`` instead of ``1 - security / 10``, because a
  border scan gets DENSER as the destination gets more lawful where a venue sale
  gets noticed in laxer space. If that inverts, the feature is backwards and
  nothing else in the stack would notice.
* **Weight parity.** "Same model, one term flipped" is only true if it stays
  true; a divergence here would silently create a second balance surface.
* **Worst-aboard severity.** A transit scan sweeps the whole hold, so unlike
  ``sell`` there is no single traded commodity to key the fine multiplier and
  heat flip on. The pick must be the worst thing aboard and must not depend on
  cargo insertion order.
* **The [OPEN-9] cooldown.** "One scan per sector per traversal" — a bad window
  check either lets a border-pacing player farm rolls or grants blanket immunity.

No real DB: every function under test is pure or reads only plain attributes, so
a ``SimpleNamespace`` stand-in is enough (same approach as
``test_admin_colonies_morale.py`` / ``test_bounty_service_nh2.py``).
"""
import itertools
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.core.illegal_commodities import ENABLED_COMMODITIES, get_meta
from src.services.contraband_service import (
    DETECT_PROB_MAX,
    TRANSIT_SCAN_COOLDOWN_SECONDS,
    ContrabandService,
)


@pytest.fixture
def svc():
    """The service with no session. Every method exercised here is pure or reads
    only attributes off objects passed in, so the DB is genuinely unused."""
    return ContrabandService(None)


def p_transit(svc, security, *, value=0, cap=50, rep=0):
    return svc._transit_detection_probability(
        illegal_value=value,
        cargo_capacity=cap,
        destination_security=security,
        personal_reputation=rep,
    )


def p_sell(svc, security, *, value=0, cap=50, rep=0):
    return svc._detection_probability(
        illegal_value=value,
        cargo_capacity=cap,
        sector=SimpleNamespace(security_level=security),
        personal_reputation=rep,
    )


# Reputation at which the shared model is NOT pinned to its 0.95 ceiling.
#
# The sell-side model saturates for any player at or below neutral reputation:
# its rep term is ``1 - rep/1000`` == 1.0 at rep 0 (the column default) and
# ``DETECT_REP_WEIGHT`` is 1.0, so base(0.05) + rep(1.0) already exceeds the cap
# before the cargo and sector terms contribute anything. That is a PRE-EXISTING
# property of the shipped sell path — WO-K2 changed neither the constants nor
# ``_detection_probability`` — and it is escalated separately as a balance
# question ([OPEN-5]). It matters HERE only because a saturated model makes every
# sector term unobservable, so these tests deliberately sample the slice of the
# reputation range where the term is live. If the model is later rebalanced these
# assertions hold unchanged; only the constant below would loosen.
UNSATURATED_REP = 1000


class TestPolarityFlip:
    """The transit model must be the sell model's mirror image in security."""

    def test_transit_risk_rises_with_destination_security(self, svc):
        ps = [p_transit(svc, s, rep=UNSATURATED_REP) for s in range(1, 11)]
        assert ps == sorted(ps), f"not monotonic: {ps}"
        # Strict below the clamp — asserting strictness across the clamped tail
        # would be asserting the clamp is broken.
        below = [p for p in ps if p < DETECT_PROB_MAX]
        assert len(below) >= 8, f"clamp binding far too early: {ps}"
        assert all(a < b for a, b in zip(below, below[1:])), f"not strict: {below}"

    def test_sell_risk_still_falls_with_security(self, svc):
        """The sell path is untouched by WO-K2 and must stay inverted."""
        ps = [p_sell(svc, s, rep=UNSATURATED_REP) for s in range(1, 11)]
        assert all(a > b for a, b in zip(ps, ps[1:])), f"sell polarity changed: {ps}"

    def test_the_two_models_are_genuinely_opposed(self, svc):
        """Guards against the flip being a no-op — if someone "simplifies"
        ``_transit_detection_probability`` into a call to the sell model, every
        monotonicity test above still passes on the sell path alone."""
        lawless, lawful = 1, 9
        assert p_transit(svc, lawful, rep=UNSATURATED_REP) > p_transit(
            svc, lawless, rep=UNSATURATED_REP
        )
        assert p_sell(svc, lawful, rep=UNSATURATED_REP) < p_sell(
            svc, lawless, rep=UNSATURATED_REP
        )


class TestWeightParity:
    """Only the sector term differs: base, weights and clamps are shared."""

    @pytest.mark.parametrize("security", range(1, 10))
    def test_matching_sector_terms_give_identical_probability(self, svc, security):
        # sell at X uses (1 - X/10); transit at (10 - X) uses (10 - X)/10 — the
        # same term value, so the two probabilities must agree.
        #
        # Compared with a tolerance, not exactly, and the tolerance is REAL float
        # noise rather than slack hiding a drift: the two spellings of the same
        # term differ in the last bit (`1.0 - 0.8` is 0.19999999999999996 where
        # `2/10` is 0.2), which surfaces as a ~1.1e-16 gap at security 8. The
        # tolerance is ~12 orders of magnitude tighter than anything observable —
        # the service rounds the reported probability to 4dp and the outcome is a
        # `random() < p` draw — so any genuine reweighting still fails this.
        assert p_sell(svc, security, value=30, rep=UNSATURATED_REP) == pytest.approx(
            p_transit(svc, 10 - security, value=30, rep=UNSATURATED_REP), abs=1e-12
        )

    def test_ceiling_holds_for_a_maxed_out_villain(self, svc):
        worst = p_transit(svc, 10, value=10**9, cap=1, rep=-(10**6))
        assert worst == DETECT_PROB_MAX
        assert worst < 1.0, "a bust must never be guaranteed"


class TestWorstHeldSeverity:
    """A transit scan sweeps the hold, so consequences key on the worst line."""

    def test_severity_outranks_quantity_in_every_cargo_order(self):
        severe = [c for c in ENABLED_COMMODITIES if get_meta(c).severity.value == "SEVERE"]
        lighter = [c for c in ENABLED_COMMODITIES if get_meta(c).severity.value != "SEVERE"]
        assert severe and lighter, "catalog no longer spans >1 severity tier"

        picks = set()
        for order in itertools.permutations([severe[0], lighter[0]]):
            hold = {f"illegal:{c.value}": 1 for c in order}
            # Bury the severe line under a huge lighter one: value must not be
            # able to outrank severity.
            hold[f"illegal:{lighter[0].value}"] = 999
            got = ContrabandService._worst_held_meta(hold)
            picks.add(got[0] if got else None)
        assert picks == {severe[0]}, f"order-dependent or wrong pick: {picks}"

    def test_equal_severity_tie_is_order_independent(self):
        by_sev = defaultdict(list)
        for c in ENABLED_COMMODITIES:
            by_sev[get_meta(c).severity].append(c)
        tied = next((v for v in by_sev.values() if len(v) >= 2), None)
        assert tied is not None, "no severity tier has two members to tie"

        a, b = tied[0], tied[1]
        # Cross the quantities so total value ties exactly, leaving the outcome
        # to the deterministic final sort key rather than to dict order.
        qa, qb = get_meta(b).base_price, get_meta(a).base_price
        first = ContrabandService._worst_held_meta(
            {f"illegal:{a.value}": qa, f"illegal:{b.value}": qb}
        )
        second = ContrabandService._worst_held_meta(
            {f"illegal:{b.value}": qb, f"illegal:{a.value}": qa}
        )
        assert first[0] is second[0]

    @pytest.mark.parametrize(
        "hold,label",
        [
            ({}, "empty hold"),
            ({"ORE": 40, "FOOD": 10}, "legal cargo only"),
            ({"illegal:NOT_A_REAL_COMMODITY": 5}, "unknown illegal:* key"),
            ({"illegal:WEAPONS": 0}, "zero-unit illegal line"),
            ({"illegal:SLAVES": 9}, "permanently-disabled commodity"),
            (["illegal:WEAPONS"], "contents is not a dict"),
            (None, "contents is None"),
        ],
    )
    def test_holds_that_must_read_as_clean(self, hold, label):
        """Each of these must return None — a scan is never triggered by them.
        The SLAVES case is load-bearing: it has no trade path anywhere, and a
        transit scan must not become the one code path that reacts to it."""
        assert ContrabandService._worst_held_meta(hold) is None, label


class TestOpen9Cooldown:
    """[OPEN-9] — one scan per sector per traversal, no re-roll on re-entry."""

    NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)
    SECTOR = 42

    def _player(self, *, ago=None, sector=None, naive=False):
        at = None if ago is None else self.NOW - timedelta(seconds=ago)
        if at is not None and naive:
            at = at.replace(tzinfo=None)
        return SimpleNamespace(
            last_contraband_scan_at=at,
            last_contraband_scan_sector_id=sector,
        )

    def _blocked(self, svc, player, sector=None):
        return svc._transit_scan_on_cooldown(
            player, self.SECTOR if sector is None else sector, self.NOW
        )

    def test_null_anchor_never_blocks(self, svc):
        """Every pre-migration row is NULL/NULL. That must read as "never
        scanned" — the migration grants no retroactive immunity."""
        assert self._blocked(svc, self._player()) is False

    def test_same_sector_inside_window_blocks(self, svc):
        half = TRANSIT_SCAN_COOLDOWN_SECONDS // 2
        assert self._blocked(svc, self._player(ago=half, sector=self.SECTOR)) is True

    def test_different_sector_inside_window_does_not_block(self, svc):
        """The cooldown is PER-SECTOR: a genuinely new crossing always scans."""
        half = TRANSIT_SCAN_COOLDOWN_SECONDS // 2
        assert self._blocked(svc, self._player(ago=half, sector=7)) is False

    def test_same_sector_past_the_window_does_not_block(self, svc):
        past = TRANSIT_SCAN_COOLDOWN_SECONDS + 1
        assert self._blocked(svc, self._player(ago=past, sector=self.SECTOR)) is False

    def test_boundary_is_exclusive_at_exactly_the_window(self, svc):
        exact = TRANSIT_SCAN_COOLDOWN_SECONDS
        assert self._blocked(svc, self._player(ago=exact, sector=self.SECTOR)) is False

    def test_naive_legacy_timestamp_does_not_raise(self, svc):
        """The column is timezone-aware going forward, but a naive stamp must
        degrade to a UTC reading rather than blowing up the arrival path."""
        half = TRANSIT_SCAN_COOLDOWN_SECONDS // 2
        p = self._player(ago=half, sector=self.SECTOR, naive=True)
        assert self._blocked(svc, p) is True

    def test_half_written_anchor_does_not_block(self, svc):
        """Sector without timestamp (or vice versa) is not a usable anchor."""
        assert self._blocked(svc, self._player(sector=self.SECTOR)) is False
        assert self._blocked(svc, self._player(ago=1)) is False


class TestSecurityResolution:
    """Both sides of the transit differential resolve through one helper, so an
    unseeded sector pair reads 5 vs 5 and can never manufacture a scan."""

    @pytest.mark.parametrize(
        "sector,expected",
        [
            (None, 5),
            (SimpleNamespace(security_level=None), 5),
            (SimpleNamespace(security_level=1), 1),
            (SimpleNamespace(security_level=10), 10),
            (SimpleNamespace(security_level="not a number"), 5),
        ],
    )
    def test_security_level_defaults(self, sector, expected):
        assert ContrabandService._security_level(sector) == expected

    def test_unseeded_pair_is_not_a_scan_event(self):
        """The gate is ``dest > origin``; two defaulted sectors must tie."""
        a = ContrabandService._security_level(None)
        b = ContrabandService._security_level(SimpleNamespace(security_level=None))
        assert not (b > a)
