"""Turn accounting — the single mutation point for Player.turns.

``Player.turns`` is a regenerating BALANCE; ``Player.lifetime_turns_spent``
is the monotonic cumulative clock the ADR-0042 police arrival watchers
compare against (``arrival_turn_threshold = offense_turn + 2``). A
watcher keyed to the balance would never fire reliably — regen pushes it
back up — so every spend site MUST route through these helpers to keep
the clock honest.

Callers keep their own affordability checks and locking; these helpers
only perform the paired mutation.
"""

from src.models.player import Player


def spend_turns(player: Player, amount: int) -> None:
    """Deduct ``amount`` turns from the balance and advance the lifetime
    clock. The caller has already verified affordability."""
    player.turns -= amount
    player.lifetime_turns_spent = (player.lifetime_turns_spent or 0) + amount


def refund_turns(player: Player, amount: int) -> None:
    """Reverse a prior spend (e.g. ADR-0029 warp-gate Phase 3 cancel).
    Decrements the lifetime clock — a refunded action never happened for
    arrival-watcher purposes."""
    player.turns += amount
    player.lifetime_turns_spent = max(0, (player.lifetime_turns_spent or 0) - amount)
