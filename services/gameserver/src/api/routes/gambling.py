"""
Gambling API Routes
Handles all gambling games at SpaceDock facilities
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Literal, Optional
import random

from sqlalchemy.orm.attributes import flag_modified

from src.core.database import get_db
from src.auth.dependencies import get_current_user, get_current_player
from src.models.user import User
from src.models.player import Player


# ============================================
# BLACKJACK CONFIGURATION
# ============================================

CARD_SUITS = ['♠', '♥', '♦', '♣']
CARD_RANKS = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']
CARD_VALUES = {
    'A': 11, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7,
    '8': 8, '9': 9, '10': 10, 'J': 10, 'Q': 10, 'K': 10
}


class Card(BaseModel):
    rank: str
    suit: str
    hidden: bool = False

    @property
    def display(self) -> str:
        if self.hidden:
            return '🂠'
        return f"{self.rank}{self.suit}"

    @property
    def value(self) -> int:
        return CARD_VALUES.get(self.rank, 0)


class BlackjackDealRequest(BaseModel):
    bet_amount: int = Field(..., ge=10, le=10000)


class BlackjackActionRequest(BaseModel):
    bet_amount: int = Field(..., ge=10, le=10000)
    player_cards: list[dict]  # List of {rank, suit}
    dealer_cards: list[dict]  # List of {rank, suit, hidden}
    deck_seed: int  # For deterministic deck continuation
    action: Literal["hit", "stand", "double"]


class BlackjackResponse(BaseModel):
    player_cards: list[dict]
    dealer_cards: list[dict]
    player_total: int
    dealer_total: int  # Only visible cards total if game in progress
    player_soft: bool  # Has usable ace
    game_over: bool
    result: Optional[str] = None  # "win", "lose", "push", "blackjack", "bust"
    win_amount: int = 0
    net_result: int = 0
    new_credits: int
    deck_seed: int
    can_double: bool = False


def create_deck(seed: int) -> list[dict]:
    """Create and shuffle a deck with given seed for reproducibility"""
    deck = []
    for suit in CARD_SUITS:
        for rank in CARD_RANKS:
            deck.append({'rank': rank, 'suit': suit})
    rng = random.Random(seed)
    rng.shuffle(deck)
    return deck


def calculate_hand_total(cards: list[dict], count_hidden: bool = False) -> tuple[int, bool]:
    """Calculate blackjack hand total. Returns (total, is_soft)"""
    total = 0
    aces = 0

    for card in cards:
        if card.get('hidden', False) and not count_hidden:
            continue
        rank = card['rank']
        total += CARD_VALUES.get(rank, 0)
        if rank == 'A':
            aces += 1

    # Adjust for aces (count as 1 instead of 11 if over 21)
    soft = False
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1

    # Hand is "soft" if there's still an ace counted as 11
    if aces > 0 and total <= 21:
        soft = True

    return total, soft


def deal_card(deck: list[dict], index: int) -> dict:
    """Get a card from the deck at given index"""
    return deck[index % len(deck)]


def reconstruct_blackjack_hands(deck: list[dict], player_card_count: int):
    """Rebuild the blackjack hands deterministically from the seeded deck and
    the (server-tracked) number of player cards — NEVER from client-supplied
    cards. Deal order: player deck[0], dealer deck[1], player deck[2], dealer
    deck[3], then every subsequent draw (player hits first, dealer afterward)
    consumes the next deck index. Returns (player_cards, dealer_cards,
    next_free_index) with the dealer's hole card hidden.
    """
    player_cards = [
        {'rank': deck[0]['rank'], 'suit': deck[0]['suit']},
        {'rank': deck[2]['rank'], 'suit': deck[2]['suit']},
    ]
    hit_count = max(0, player_card_count - 2)
    for i in range(hit_count):
        c = deck[4 + i]
        player_cards.append({'rank': c['rank'], 'suit': c['suit']})
    dealer_cards = [
        {'rank': deck[1]['rank'], 'suit': deck[1]['suit'], 'hidden': False},
        {'rank': deck[3]['rank'], 'suit': deck[3]['suit'], 'hidden': True},
    ]
    next_free_index = 4 + hit_count
    return player_cards, dealer_cards, next_free_index

router = APIRouter(prefix="/gambling", tags=["gambling"])


class SlotSpinRequest(BaseModel):
    bet_amount: int = Field(..., ge=10, le=10000)


class SlotSpinResponse(BaseModel):
    reels: list[str]
    win_amount: int
    net_result: int  # positive = win, negative = loss
    new_credits: int
    jackpot: bool


class DiceRollRequest(BaseModel):
    bet_amount: int = Field(..., ge=10, le=10000)
    bet_type: Literal["high", "low", "exact"]
    exact_number: int | None = Field(None, ge=2, le=12)


class DiceRollResponse(BaseModel):
    dice: list[int]
    total: int
    win_amount: int
    net_result: int
    new_credits: int
    supernova: bool
    void: bool


class LotteryTicketRequest(BaseModel):
    numbers: list[int] = Field(..., min_length=4, max_length=4)
    bet_amount: int = Field(..., ge=100, le=5000)


class LotteryTicketResponse(BaseModel):
    player_numbers: list[int]
    winning_numbers: list[int]
    matches: int
    win_amount: int
    net_result: int
    new_credits: int
    jackpot: bool


# Slot machine configuration
SLOT_SYMBOLS = ['planet', 'star', 'ship', 'credits', 'blackhole', 'jackpot']
SYMBOL_EMOJIS = {
    'planet': '🌍',
    'star': '⭐',
    'ship': '🚀',
    'credits': '💳',
    'blackhole': '🕳️',
    'jackpot': '💎'
}

# Weighted probabilities for slot symbols (must sum to 100)
SYMBOL_WEIGHTS = {
    'planet': 25,     # Common
    'star': 25,       # Common
    'ship': 20,       # Uncommon
    'credits': 15,    # Uncommon
    'blackhole': 10,  # Rare (bad)
    'jackpot': 5      # Rare (jackpot)
}

# Payout multipliers for three-of-a-kind
SLOT_PAYOUTS = {
    'jackpot': 50,   # 💎💎💎 = 50x
    'ship': 10,      # 🚀🚀🚀 = 10x
    'star': 8,       # ⭐⭐⭐ = 8x
    'planet': 5,     # 🌍🌍🌍 = 5x
    'credits': 3,    # 💳💳💳 = 3x
    'blackhole': 0   # 🕳️🕳️🕳️ = lose
}


def weighted_random_symbol() -> str:
    """Select a random slot symbol based on weighted probabilities"""
    choices = []
    for symbol, weight in SYMBOL_WEIGHTS.items():
        choices.extend([symbol] * weight)
    return random.choice(choices)


@router.post("/slots/spin", response_model=SlotSpinResponse)
async def spin_slots(
    request: SlotSpinRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player)
):
    """Spin the cosmic slot machine"""

    # Validate player has enough credits
    if current_player.credits < request.bet_amount:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient credits. Need {request.bet_amount}, have {current_player.credits}"
        )

    # Verify player is docked at a SpaceDock (has gambling)
    if not current_player.is_docked:
        raise HTTPException(
            status_code=400,
            detail="You must be docked at a SpaceDock to gamble"
        )

    # Deduct bet amount
    current_player.credits -= request.bet_amount

    # Spin the reels
    reels = [weighted_random_symbol() for _ in range(3)]
    reel_emojis = [SYMBOL_EMOJIS[r] for r in reels]

    # Calculate winnings
    win_amount = 0
    jackpot = False

    # Three of a kind
    if reels[0] == reels[1] == reels[2]:
        multiplier = SLOT_PAYOUTS.get(reels[0], 0)
        win_amount = request.bet_amount * multiplier
        jackpot = reels[0] == 'jackpot'
    # Two matching (partial win)
    elif reels[0] == reels[1] or reels[1] == reels[2]:
        # 50% of bet returned
        win_amount = request.bet_amount // 2
    # Black hole penalty - already lost the bet, no additional penalty

    # Add winnings
    current_player.credits += win_amount

    # Calculate net result
    net_result = win_amount - request.bet_amount

    db.commit()

    return SlotSpinResponse(
        reels=reel_emojis,
        win_amount=win_amount,
        net_result=net_result,
        new_credits=current_player.credits,
        jackpot=jackpot
    )


@router.post("/dice/roll", response_model=DiceRollResponse)
async def roll_dice(
    request: DiceRollRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player)
):
    """Roll the nebula dice"""

    # Validate exact number for exact bets
    if request.bet_type == "exact" and request.exact_number is None:
        raise HTTPException(
            status_code=400,
            detail="exact_number is required for exact bet type"
        )

    # Validate player has enough credits
    if current_player.credits < request.bet_amount:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient credits. Need {request.bet_amount}, have {current_player.credits}"
        )

    # Verify player is docked
    if not current_player.is_docked:
        raise HTTPException(
            status_code=400,
            detail="You must be docked at a SpaceDock to gamble"
        )

    # Deduct bet amount
    current_player.credits -= request.bet_amount

    # Roll the dice
    die1 = random.randint(1, 6)
    die2 = random.randint(1, 6)
    total = die1 + die2

    # Check for special conditions
    supernova = die1 == 6 and die2 == 6  # Double 6s
    void = total == 7  # The Void

    # Calculate winnings
    win_amount = 0

    if supernova:
        # Supernova always pays 35x regardless of bet type
        win_amount = request.bet_amount * 35
    elif void:
        # The Void - house always wins
        win_amount = 0
    elif request.bet_type == "high" and 8 <= total <= 12:
        win_amount = request.bet_amount * 2
    elif request.bet_type == "low" and 2 <= total <= 6:
        win_amount = request.bet_amount * 2
    elif request.bet_type == "exact" and total == request.exact_number:
        # Payout based on probability
        exact_payouts = {
            2: 35, 3: 17, 4: 11, 5: 8, 6: 6,
            7: 5,  # Very rare to bet on 7 and win (supernova only)
            8: 6, 9: 8, 10: 11, 11: 17, 12: 35
        }
        win_amount = request.bet_amount * exact_payouts.get(request.exact_number, 5)

    # Add winnings
    current_player.credits += win_amount

    # Calculate net result
    net_result = win_amount - request.bet_amount

    db.commit()

    return DiceRollResponse(
        dice=[die1, die2],
        total=total,
        win_amount=win_amount,
        net_result=net_result,
        new_credits=current_player.credits,
        supernova=supernova,
        void=void
    )


# Lottery configuration
LOTTERY_PAYOUTS = {
    4: 1000,  # All 4 match = 1000x (Jackpot!)
    3: 50,    # 3 match = 50x
    2: 5,     # 2 match = 5x
    1: 1,     # 1 match = return bet
    0: 0      # No match = lose
}


@router.post("/lottery/buy-ticket", response_model=LotteryTicketResponse)
async def buy_lottery_ticket(
    request: LotteryTicketRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player)
):
    """Buy a Sector Sweep lottery ticket"""

    # Validate player numbers are in valid range (1-12 for sectors)
    for num in request.numbers:
        if num < 1 or num > 12:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid sector number: {num}. Must be between 1 and 12"
            )

    # Check for duplicate numbers
    if len(set(request.numbers)) != 4:
        raise HTTPException(
            status_code=400,
            detail="All 4 sector numbers must be unique"
        )

    # Validate player has enough credits
    if current_player.credits < request.bet_amount:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient credits. Need {request.bet_amount}, have {current_player.credits}"
        )

    # Verify player is docked
    if not current_player.is_docked:
        raise HTTPException(
            status_code=400,
            detail="You must be docked at a SpaceDock to gamble"
        )

    # Deduct bet amount
    current_player.credits -= request.bet_amount

    # Generate winning numbers (4 unique numbers from 1-12)
    winning_numbers = random.sample(range(1, 13), 4)

    # Count matches
    matches = len(set(request.numbers) & set(winning_numbers))

    # Calculate winnings
    multiplier = LOTTERY_PAYOUTS.get(matches, 0)
    win_amount = request.bet_amount * multiplier
    jackpot = matches == 4

    # Add winnings
    current_player.credits += win_amount

    # Calculate net result
    net_result = win_amount - request.bet_amount

    db.commit()

    return LotteryTicketResponse(
        player_numbers=request.numbers,
        winning_numbers=winning_numbers,
        matches=matches,
        win_amount=win_amount,
        net_result=net_result,
        new_credits=current_player.credits,
        jackpot=jackpot
    )


# ============================================
# STELLAR BLACKJACK ENDPOINTS
# ============================================

@router.post("/blackjack/deal", response_model=BlackjackResponse)
async def blackjack_deal(
    request: BlackjackDealRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player)
):
    """Start a new blackjack hand"""

    # Validate player has enough credits
    if current_player.credits < request.bet_amount:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient credits. Need {request.bet_amount}, have {current_player.credits}"
        )

    # Verify player is docked
    if not current_player.is_docked:
        raise HTTPException(
            status_code=400,
            detail="You must be docked at a SpaceDock to gamble"
        )

    # Lock the player row so the bet deduction + active-game write are atomic
    # against a concurrent deal/action (no double-spend, no two live games).
    current_player = db.query(Player).filter(
        Player.id == current_player.id
    ).populate_existing().with_for_update().first()
    if current_player.credits < request.bet_amount:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient credits. Need {request.bet_amount}, have {current_player.credits}"
        )

    # Deduct bet amount
    current_player.credits -= request.bet_amount

    # Create a new shuffled deck with a SERVER-chosen seed (never client-supplied)
    deck_seed = random.randint(1, 1000000)
    deck = create_deck(deck_seed)

    # Deal initial cards: player gets 2, dealer gets 2 (one hidden)
    player_cards = [
        {'rank': deck[0]['rank'], 'suit': deck[0]['suit']},
        {'rank': deck[2]['rank'], 'suit': deck[2]['suit']}
    ]
    dealer_cards = [
        {'rank': deck[1]['rank'], 'suit': deck[1]['suit'], 'hidden': False},
        {'rank': deck[3]['rank'], 'suit': deck[3]['suit'], 'hidden': True}
    ]

    player_total, player_soft = calculate_hand_total(player_cards)
    dealer_visible_total, _ = calculate_hand_total(dealer_cards, count_hidden=False)

    # Check for natural blackjack
    game_over = False
    result = None
    win_amount = 0

    if player_total == 21:
        # Player has blackjack - reveal dealer cards
        dealer_cards[1]['hidden'] = False
        dealer_total, _ = calculate_hand_total(dealer_cards, count_hidden=True)

        if dealer_total == 21:
            # Both have blackjack - push
            result = "push"
            win_amount = request.bet_amount  # Return bet
        else:
            # Player wins with blackjack (pays 3:2)
            result = "blackjack"
            win_amount = int(request.bet_amount * 2.5)

        game_over = True
        current_player.credits += win_amount

    # Persist the authoritative game state server-side so /blackjack/action can
    # rebuild the hands from the seed (never trusting client-sent cards) and so
    # a payout cannot be claimed without a real, un-settled deal. Cleared the
    # moment the hand ends (here on a natural blackjack).
    settings = dict(current_player.settings or {})
    if game_over:
        settings.pop('blackjack_game', None)
    else:
        settings['blackjack_game'] = {
            'deck_seed': deck_seed,
            'bet_amount': request.bet_amount,
            'player_card_count': len(player_cards),
        }
    current_player.settings = settings
    flag_modified(current_player, 'settings')

    db.commit()

    net_result = win_amount - request.bet_amount if game_over else 0

    return BlackjackResponse(
        player_cards=player_cards,
        dealer_cards=dealer_cards,
        player_total=player_total,
        dealer_total=dealer_visible_total if not game_over else calculate_hand_total(dealer_cards, True)[0],
        player_soft=player_soft,
        game_over=game_over,
        result=result,
        win_amount=win_amount,
        net_result=net_result,
        new_credits=current_player.credits,
        deck_seed=deck_seed,
        can_double=not game_over and len(player_cards) == 2
    )


@router.post("/blackjack/action", response_model=BlackjackResponse)
async def blackjack_action(
    request: BlackjackActionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player)
):
    """Perform a blackjack action (hit, stand, or double)"""

    # Verify player is docked
    if not current_player.is_docked:
        raise HTTPException(
            status_code=400,
            detail="You must be docked at a SpaceDock to gamble"
        )

    # Lock the player + load the authoritative active game. No active game means
    # no real un-settled deal — reject (closes the "/action without /deal" and
    # replay-a-settled-hand credit faucets).
    current_player = db.query(Player).filter(
        Player.id == current_player.id
    ).populate_existing().with_for_update().first()
    game = (current_player.settings or {}).get('blackjack_game')
    if not game:
        raise HTTPException(status_code=400, detail="No active blackjack hand — deal first.")

    # Rebuild the deck + hands from the SERVER-stored seed and player-card count.
    # Client-sent cards / seed / bet are IGNORED (anti-fabrication / anti-inflation):
    # cards_dealt is the next free deck index from the deterministic deal order.
    deck = create_deck(int(game['deck_seed']))
    bet_amount = int(game['bet_amount'])
    player_cards, dealer_cards, cards_dealt = reconstruct_blackjack_hands(
        deck, int(game['player_card_count'])
    )

    game_over = False
    result = None
    win_amount = 0

    if request.action == "double":
        # Double down: double bet, take one card, then stand
        if len(player_cards) != 2:
            raise HTTPException(status_code=400, detail="Can only double on first two cards")

        if current_player.credits < bet_amount:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient credits to double. Need {bet_amount}, have {current_player.credits}"
            )

        # Deduct additional bet (the stored stake, never a client-sent amount)
        current_player.credits -= bet_amount
        bet_amount = bet_amount * 2

        # Deal one card to player
        new_card = deck[cards_dealt]
        player_cards.append({'rank': new_card['rank'], 'suit': new_card['suit']})
        cards_dealt += 1

        # Force stand after double
        request.action = "stand"

    if request.action == "hit":
        # Deal one card to player
        new_card = deck[cards_dealt]
        player_cards.append({'rank': new_card['rank'], 'suit': new_card['suit']})
        cards_dealt += 1

        player_total, player_soft = calculate_hand_total(player_cards)

        if player_total > 21:
            # Player busts
            game_over = True
            result = "bust"
            win_amount = 0
            # Reveal dealer's hidden card
            for card in dealer_cards:
                card['hidden'] = False

    if request.action == "stand" or (request.action != "hit" and game_over is False):
        # Player stands - dealer plays
        game_over = True

        # Reveal dealer's hidden card
        for card in dealer_cards:
            card['hidden'] = False

        player_total, player_soft = calculate_hand_total(player_cards)
        dealer_total, _ = calculate_hand_total(dealer_cards, count_hidden=True)

        # Dealer must hit on 16 or less, stand on 17+
        while dealer_total < 17:
            new_card = deck[cards_dealt]
            dealer_cards.append({'rank': new_card['rank'], 'suit': new_card['suit'], 'hidden': False})
            cards_dealt += 1
            dealer_total, _ = calculate_hand_total(dealer_cards, count_hidden=True)

        # Determine winner. A busted player ALWAYS loses — this covers the
        # double-into-bust path, which forces a stand without re-entering the
        # hit-bust check (a busted hand must never out-rank the dealer).
        if player_total > 21:
            result = "bust"
            win_amount = 0
        elif dealer_total > 21:
            result = "win"
            win_amount = bet_amount * 2
        elif dealer_total > player_total:
            result = "lose"
            win_amount = 0
        elif player_total > dealer_total:
            result = "win"
            win_amount = bet_amount * 2
        else:
            result = "push"
            win_amount = bet_amount  # Return original bet

        current_player.credits += win_amount

    # Persist or clear the authoritative game state: clear on game over, else
    # remember the new player-card count so the next action rebuilds correctly.
    settings = dict(current_player.settings or {})
    if game_over:
        settings.pop('blackjack_game', None)
    else:
        settings['blackjack_game'] = {
            'deck_seed': int(game['deck_seed']),
            'bet_amount': int(game['bet_amount']),
            'player_card_count': len(player_cards),
        }
    current_player.settings = settings
    flag_modified(current_player, 'settings')

    # Calculate final totals
    player_total, player_soft = calculate_hand_total(player_cards)
    if game_over:
        dealer_total, _ = calculate_hand_total(dealer_cards, count_hidden=True)
    else:
        dealer_total, _ = calculate_hand_total(dealer_cards, count_hidden=False)

    db.commit()

    net_result = win_amount - bet_amount if game_over else 0

    return BlackjackResponse(
        player_cards=player_cards,
        dealer_cards=dealer_cards,
        player_total=player_total,
        dealer_total=dealer_total,
        player_soft=player_soft,
        game_over=game_over,
        result=result,
        win_amount=win_amount,
        net_result=net_result,
        new_credits=current_player.credits,
        deck_seed=int(game['deck_seed']),
        can_double=not game_over and len(player_cards) == 2
    )
