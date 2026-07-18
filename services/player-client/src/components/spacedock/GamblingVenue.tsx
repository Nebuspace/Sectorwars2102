import React from 'react';
import { formatCredits } from '../../utils/formatters';
import './spacedock.css';

// =====================================================================
// Gambling Hall — extracted verbatim from SpaceDockInterface's inline
// `renderGamblingHall()` closure (WO-UI3-VENUES sub-part #1, pure
// refactor — zero behavior change). All state/handlers remain owned by
// SpaceDockInterface and are threaded through as props here. The only
// departure from a literal copy is the header back-button's two-mode
// handler, which is reconstructed locally from the same
// `currentGame`/`setCurrentGame`/`setLastWin`/`onBack` props the inline
// closure itself closed over (identical resulting behavior).
// =====================================================================

export type GamblingGame = 'menu' | 'slots' | 'dice' | 'blackjack' | 'lottery';

// Blackjack card types — mirror SpaceDockInterface.tsx's identically-named
// interfaces.
export interface BlackjackCard {
  rank: string;
  suit: string;
  hidden?: boolean;
}

export interface BlackjackGameState {
  playerCards: BlackjackCard[];
  dealerCards: BlackjackCard[];
  playerTotal: number;
  dealerTotal: number;
  gameOver: boolean;
  result: string | null;
  canDouble: boolean;
  deckSeed: number;
}

const renderCard = (card: BlackjackCard, index: number) => {
  const isRed = card.suit === '♥' || card.suit === '♦';
  if (card.hidden) {
    return (
      <div key={index} className="playing-card hidden">
        <div className="card-back">🂠</div>
      </div>
    );
  }
  return (
    <div key={index} className={`playing-card ${isRed ? 'red' : 'black'}`}>
      <div className="card-corner top">
        <span className="card-rank">{card.rank}</span>
        <span className="card-suit">{card.suit}</span>
      </div>
      <div className="card-center">{card.suit}</div>
      <div className="card-corner bottom">
        <span className="card-rank">{card.rank}</span>
        <span className="card-suit">{card.suit}</span>
      </div>
    </div>
  );
};

interface GamblingVenueProps {
  onBack: () => void;
  displayCredits: number;
  gamblingError: string | null;

  currentGame: GamblingGame;
  setCurrentGame: (game: GamblingGame) => void;

  betAmount: number;
  setBetAmount: (amount: number) => void;

  // Slots
  slotReels: string[];
  isSpinning: boolean;
  isJackpot: boolean;
  lastWin: number | null;
  setLastWin: (win: number | null) => void;
  spinSlots: () => void;

  // Dice
  diceValues: number[];
  diceBetType: 'high' | 'low' | 'exact';
  setDiceBetType: (type: 'high' | 'low' | 'exact') => void;
  diceExactBet: number;
  setDiceExactBet: (num: number) => void;
  isSupernova: boolean;
  isVoid: boolean;
  rollDice: () => void;

  // Blackjack
  blackjackGame: BlackjackGameState | null;
  setBlackjackGame: (game: BlackjackGameState | null) => void;
  isBlackjackDealing: boolean;
  dealBlackjack: () => void;
  blackjackAction: (action: 'hit' | 'stand' | 'double') => void;

  // Lottery
  lotteryNumbers: number[];
  setLotteryNumbers: (numbers: number[]) => void;
  winningNumbers: number[];
  setWinningNumbers: (numbers: number[]) => void;
  lotteryMatches: number | null;
  setLotteryMatches: (matches: number | null) => void;
  isLotteryPlaying: boolean;
  toggleLotteryNumber: (num: number) => void;
  playLottery: () => void;

  blackMarketButton: React.ReactNode;
}

const GamblingVenue: React.FC<GamblingVenueProps> = ({
  onBack,
  displayCredits,
  gamblingError,
  currentGame,
  setCurrentGame,
  betAmount,
  setBetAmount,
  slotReels,
  isSpinning,
  isJackpot,
  lastWin,
  setLastWin,
  spinSlots,
  diceValues,
  diceBetType,
  setDiceBetType,
  diceExactBet,
  setDiceExactBet,
  isSupernova,
  isVoid,
  rollDice,
  blackjackGame,
  setBlackjackGame,
  isBlackjackDealing,
  dealBlackjack,
  blackjackAction,
  lotteryNumbers,
  setLotteryNumbers,
  winningNumbers,
  setWinningNumbers,
  lotteryMatches,
  setLotteryMatches,
  isLotteryPlaying,
  toggleLotteryNumber,
  playLottery,
  blackMarketButton,
}) => (
  <div className="venue-container gambling">
    <div className="venue-header">
      <button className="back-button" onClick={() => {
        if (currentGame === 'menu') {
          onBack();
        } else {
          setCurrentGame('menu');
          setLastWin(null);
        }
      }}>
        ← {currentGame === 'menu' ? 'Back to Hub' : 'Back to Games'}
      </button>
      <h2>🎰 Gambling Hall</h2>
    </div>

    <div className="venue-content-area gambling-area">
      {currentGame === 'menu' && (
        <div className="gambling-menu">
          <div className="gambling-welcome">
            <div className="neon-sign">FORTUNE FAVORS THE BOLD</div>
            <p>Choose your game and test your luck among the stars...</p>
          </div>

          <div className="games-grid">
            <div
              className="game-card slots"
              onClick={() => setCurrentGame('slots')}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  setCurrentGame('slots');
                }
              }}
            >
              <div className="game-icon">🎰</div>
              <h3>Cosmic Slots</h3>
              <p>Match symbols to win big! Jackpot pays 50x</p>
              <div className="game-stats">
                <span>Min Bet: {formatCredits(10)}</span>
                <span>Max Win: 50x</span>
              </div>
            </div>

            <div
              className="game-card dice"
              onClick={() => setCurrentGame('dice')}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  setCurrentGame('dice');
                }
              }}
            >
              <div className="game-icon">🎲</div>
              <h3>Nebula Dice</h3>
              <p>Bet high, low, or exact. Avoid the Void!</p>
              <div className="game-stats">
                <span>Min Bet: {formatCredits(10)}</span>
                <span>Max Win: 35x</span>
              </div>
            </div>

            <div
              className="game-card blackjack"
              onClick={() => setCurrentGame('blackjack')}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  setCurrentGame('blackjack');
                }
              }}
            >
              <div className="game-icon">🃏</div>
              <h3>Stellar Blackjack</h3>
              <p>Beat the dealer to 21 without busting!</p>
              <div className="game-stats">
                <span>Min Bet: {formatCredits(10)}</span>
                <span>Blackjack: 3:2</span>
              </div>
            </div>

            <div
              className="game-card lottery"
              onClick={() => setCurrentGame('lottery')}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  setCurrentGame('lottery');
                }
              }}
            >
              <div className="game-icon">🎫</div>
              <h3>Sector Sweep</h3>
              <p>Pick sectors, match the draw, win the jackpot!</p>
              <div className="game-stats">
                <span>Ticket: {formatCredits(100)}</span>
                <span>Jackpot: {formatCredits(1000000)}</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {currentGame === 'slots' && (
        <div className="game-view slots-game">
          <div className="slot-machine">
            <div className="slot-header">
              <h3>COSMIC SLOTS</h3>
              <div className="jackpot-display">
                JACKPOT: <span className="jackpot-amount">💎💎💎 = 50x</span>
              </div>
            </div>

            {gamblingError && (
              <div className="gambling-error">{gamblingError}</div>
            )}

            {isJackpot && lastWin !== null && lastWin > 0 && (
              <div className="jackpot-alert">🎉 JACKPOT! 🎉</div>
            )}

            <div className="slot-reels">
              {slotReels.map((symbol, idx) => (
                <div key={idx} className={`reel ${isSpinning ? 'spinning' : ''} ${isJackpot ? 'jackpot' : ''}`}>
                  <span className="symbol">{symbol}</span>
                </div>
              ))}
            </div>

            <div className="slot-result">
              {lastWin !== null && (
                <div className={`win-display ${lastWin > 0 ? 'winner' : lastWin < 0 ? 'loser' : 'push'}`}>
                  {lastWin > 0 ? `WIN! +${formatCredits(lastWin)}!` :
                   lastWin < 0 ? `Lost ${formatCredits(Math.abs(lastWin))}` :
                   'No match - try again!'}
                </div>
              )}
            </div>

            <div className="slot-controls">
              <div className="bet-selector">
                <label>Bet Amount:</label>
                <div className="bet-buttons">
                  {[10, 50, 100, 500, 1000].map(amount => (
                    <button
                      key={amount}
                      className={`bet-btn ${betAmount === amount ? 'selected' : ''}`}
                      onClick={() => setBetAmount(amount)}
                      disabled={isSpinning}
                    >
                      {amount}
                    </button>
                  ))}
                </div>
              </div>

              <button
                className="spin-button"
                onClick={spinSlots}
                disabled={isSpinning || displayCredits < betAmount}
              >
                {isSpinning ? 'SPINNING...' : 'SPIN'}
              </button>
            </div>

            <div className="paytable">
              <h4>Payouts</h4>
              <div className="paytable-grid">
                <span>💎💎💎 = 50x</span>
                <span>🚀🚀🚀 = 10x</span>
                <span>⭐⭐⭐ = 8x</span>
                <span>🌍🌍🌍 = 5x</span>
                <span>💳💳💳 = 3x</span>
                <span>2 Match = 0.5x</span>
                <span>🕳️ = Lose</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {currentGame === 'dice' && (
        <div className="game-view dice-game">
          <div className="dice-table">
            <div className="dice-header">
              <h3>NEBULA DICE</h3>
              <p className="dice-subtitle">Roll the cosmic dice. Beware the Void (7)!</p>
            </div>

            {gamblingError && (
              <div className="gambling-error">{gamblingError}</div>
            )}

            <div className="dice-display">
              <div className={`die ${diceValues[0] > 0 ? 'rolled' : ''} ${isSupernova ? 'supernova' : ''} ${isVoid ? 'void' : ''}`}>
                {diceValues[0] > 0 ? diceValues[0] : '?'}
              </div>
              <div className="dice-plus">+</div>
              <div className={`die ${diceValues[1] > 0 ? 'rolled' : ''} ${isSupernova ? 'supernova' : ''} ${isVoid ? 'void' : ''}`}>
                {diceValues[1] > 0 ? diceValues[1] : '?'}
              </div>
              <div className="dice-equals">=</div>
              <div className={`dice-total ${isVoid ? 'void' : ''}`}>
                {diceValues[0] + diceValues[1] > 0 ? diceValues[0] + diceValues[1] : '?'}
              </div>
            </div>

            {isSupernova && (
              <div className="supernova-alert">🌟 SUPERNOVA! 🌟</div>
            )}

            {isVoid && (
              <div className="void-alert">🕳️ THE VOID 🕳️</div>
            )}

            <div className="dice-result">
              {lastWin !== null && (
                <div className={`win-display ${lastWin > 0 ? 'winner' : 'loser'}`}>
                  {lastWin > 0 ? `WIN! +${formatCredits(lastWin)}!` :
                   `Lost ${formatCredits(Math.abs(lastWin))}`}
                </div>
              )}
            </div>

            <div className="dice-betting">
              <div className="bet-type-selector">
                <label>Bet Type:</label>
                <div className="bet-type-buttons">
                  <button
                    className={`type-btn ${diceBetType === 'low' ? 'selected' : ''}`}
                    onClick={() => setDiceBetType('low')}
                  >
                    LOW (2-6) 2x
                  </button>
                  <button
                    className={`type-btn ${diceBetType === 'high' ? 'selected' : ''}`}
                    onClick={() => setDiceBetType('high')}
                  >
                    HIGH (8-12) 2x
                  </button>
                  <button
                    className={`type-btn ${diceBetType === 'exact' ? 'selected' : ''}`}
                    onClick={() => setDiceBetType('exact')}
                  >
                    EXACT (5-35x)
                  </button>
                </div>
              </div>

              {diceBetType === 'exact' && (
                <div className="exact-number-selector">
                  <label>Pick your number:</label>
                  <div className="number-buttons">
                    {[2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].map(num => (
                      <button
                        key={num}
                        className={`num-btn ${diceExactBet === num ? 'selected' : ''} ${num === 7 ? 'void' : ''}`}
                        onClick={() => setDiceExactBet(num)}
                      >
                        {num}
                      </button>
                    ))}
                  </div>
                  <div className="exact-payout">
                    Payout: {diceExactBet === 2 || diceExactBet === 12 ? '35x' :
                             diceExactBet === 3 || diceExactBet === 11 ? '17x' :
                             diceExactBet === 4 || diceExactBet === 10 ? '11x' :
                             diceExactBet === 5 || diceExactBet === 9 ? '8x' :
                             diceExactBet === 6 || diceExactBet === 8 ? '6x' : '5x'}
                  </div>
                </div>
              )}

              <div className="bet-amount-selector">
                <label>Bet Amount:</label>
                <div className="bet-buttons">
                  {[10, 50, 100, 500, 1000].map(amount => (
                    <button
                      key={amount}
                      className={`bet-btn ${betAmount === amount ? 'selected' : ''}`}
                      onClick={() => setBetAmount(amount)}
                    >
                      {amount}
                    </button>
                  ))}
                </div>
              </div>

              <button
                className="roll-button"
                onClick={rollDice}
                disabled={displayCredits < betAmount}
              >
                ROLL THE DICE
              </button>
            </div>

            <div className="dice-rules">
              <h4>Rules</h4>
              <ul>
                <li><strong>7 = The Void</strong> - House wins on any bet</li>
                <li><strong>Double 6s = Supernova</strong> - Pays 35x regardless of bet type!</li>
                <li>High/Low bets pay 2x your wager</li>
              </ul>
            </div>
          </div>
        </div>
      )}

      {currentGame === 'blackjack' && (
        <div className="game-view blackjack-game">
          <div className="blackjack-table">
            <div className="blackjack-header">
              <h3>STELLAR BLACKJACK</h3>
              <div className="blackjack-payout-info">
                <span>Blackjack pays 3:2</span>
                <span>Dealer stands on 17</span>
              </div>
            </div>

            {gamblingError && (
              <div className="gambling-error">{gamblingError}</div>
            )}

            {!blackjackGame ? (
              <div className="blackjack-start">
                <div className="blackjack-rules">
                  <h4>How to Play</h4>
                  <ul>
                    <li>Get closer to 21 than the dealer without going over</li>
                    <li>Face cards (J, Q, K) are worth 10</li>
                    <li>Aces are worth 11 or 1</li>
                    <li>Blackjack (Ace + 10-card) pays 3:2</li>
                    <li>Double down doubles your bet and gives one more card</li>
                  </ul>
                </div>

                <div className="bet-selector blackjack-bet">
                  <label>Bet Amount:</label>
                  <div className="bet-buttons">
                    {[10, 50, 100, 500, 1000].map(amount => (
                      <button
                        key={amount}
                        className={`bet-btn ${betAmount === amount ? 'selected' : ''}`}
                        onClick={() => setBetAmount(amount)}
                        disabled={isBlackjackDealing}
                      >
                        {amount}
                      </button>
                    ))}
                  </div>
                </div>

                <button
                  className="deal-button"
                  onClick={dealBlackjack}
                  disabled={isBlackjackDealing || displayCredits < betAmount}
                >
                  {isBlackjackDealing ? 'DEALING...' : 'DEAL CARDS'}
                </button>
              </div>
            ) : (
              <div className="blackjack-game-area">
                {/* Dealer's Hand */}
                <div className="hand dealer-hand">
                  <div className="hand-label">
                    Dealer
                    {blackjackGame.gameOver && (
                      <span className="hand-total">({blackjackGame.dealerTotal})</span>
                    )}
                  </div>
                  <div className="cards">
                    {blackjackGame.dealerCards.map((card, idx) => renderCard(card, idx))}
                  </div>
                </div>

                {/* Result Display */}
                {blackjackGame.gameOver && (
                  <div className={`blackjack-result ${blackjackGame.result}`}>
                    {blackjackGame.result === 'blackjack' && '🎰 BLACKJACK! 🎰'}
                    {blackjackGame.result === 'win' && '🎉 YOU WIN! 🎉'}
                    {blackjackGame.result === 'lose' && '😢 Dealer Wins'}
                    {blackjackGame.result === 'push' && '🤝 Push - Tie Game'}
                    {blackjackGame.result === 'bust' && '💥 BUST!'}
                    {lastWin !== null && (
                      <div className="result-amount">
                        {lastWin > 0 ? `+${formatCredits(lastWin)}` : formatCredits(lastWin)}
                      </div>
                    )}
                  </div>
                )}

                {/* Player's Hand */}
                <div className="hand player-hand">
                  <div className="hand-label">
                    Your Hand
                    <span className="hand-total">({blackjackGame.playerTotal})</span>
                  </div>
                  <div className="cards">
                    {blackjackGame.playerCards.map((card, idx) => renderCard(card, idx))}
                  </div>
                </div>

                {/* Action Buttons */}
                <div className="blackjack-controls">
                  {!blackjackGame.gameOver ? (
                    <>
                      <button
                        className="action-btn hit"
                        onClick={() => blackjackAction('hit')}
                      >
                        HIT
                      </button>
                      <button
                        className="action-btn stand"
                        onClick={() => blackjackAction('stand')}
                      >
                        STAND
                      </button>
                      {blackjackGame.canDouble && displayCredits >= betAmount && (
                        <button
                          className="action-btn double"
                          onClick={() => blackjackAction('double')}
                        >
                          DOUBLE DOWN
                        </button>
                      )}
                    </>
                  ) : (
                    <button
                      className="deal-button new-hand"
                      onClick={() => {
                        setBlackjackGame(null);
                        setLastWin(null);
                      }}
                    >
                      NEW HAND
                    </button>
                  )}
                </div>

                <div className="current-bet-display">
                  Current Bet: {formatCredits(betAmount)}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {currentGame === 'lottery' && (
        <div className="game-view lottery-game">
          <div className="lottery-booth">
            <div className="lottery-header">
              <h3>SECTOR SWEEP</h3>
              <div className="jackpot-banner">
                <span className="jp-label">JACKPOT</span>
                <span className="jp-amount">1000x BET</span>
              </div>
            </div>

            <div className="lottery-info">
              <p>Pick 4 sectors from the grid below. Match to win!</p>
              <div className="prize-table">
                <span>1 Match: 1x</span>
                <span>2 Match: 5x</span>
                <span>3 Match: 50x</span>
                <span>4 Match: 1000x!</span>
              </div>
            </div>

            {gamblingError && (
              <div className="gambling-error">{gamblingError}</div>
            )}

            <div className="lottery-selections">
              <p>Your Selections ({lotteryNumbers.length}/4):</p>
              <div className="selected-numbers">
                {lotteryNumbers.length > 0 ? (
                  lotteryNumbers.map(n => (
                    <span key={n} className="selected-num">{n}</span>
                  ))
                ) : (
                  <span className="no-selection">Pick 4 sectors below</span>
                )}
              </div>
            </div>

            <div className="sector-grid">
              {Array.from({ length: 12 }, (_, i) => (
                <button
                  key={i + 1}
                  className={`sector-pick ${lotteryNumbers.includes(i + 1) ? 'selected' : ''} ${winningNumbers.includes(i + 1) ? 'winning' : ''}`}
                  onClick={() => toggleLotteryNumber(i + 1)}
                  disabled={isLotteryPlaying}
                >
                  {i + 1}
                </button>
              ))}
            </div>

            {winningNumbers.length > 0 && (
              <div className="lottery-results">
                <div className="winning-numbers-display">
                  <p>Winning Sectors:</p>
                  <div className="winning-nums">
                    {winningNumbers.map(n => (
                      <span
                        key={n}
                        className={`winning-num ${lotteryNumbers.includes(n) ? 'matched' : ''}`}
                      >
                        {n}
                      </span>
                    ))}
                  </div>
                </div>
                <div className={`lottery-result-text ${lotteryMatches && lotteryMatches > 0 ? 'winner' : 'loser'}`}>
                  {isJackpot ? (
                    <div className="jackpot-win">🎉 JACKPOT! 🎉</div>
                  ) : lotteryMatches && lotteryMatches > 0 ? (
                    `${lotteryMatches} Match${lotteryMatches > 1 ? 'es' : ''}! +${formatCredits(lastWin)}!`
                  ) : (
                    `No matches. Lost ${formatCredits(betAmount)}`
                  )}
                </div>
              </div>
            )}

            <div className="lottery-controls">
              <div className="bet-selector lottery-bet">
                <label>Ticket Price:</label>
                <div className="bet-buttons">
                  {[100, 250, 500, 1000, 2500].map(amount => (
                    <button
                      key={amount}
                      className={`bet-btn ${betAmount === amount ? 'selected' : ''}`}
                      onClick={() => setBetAmount(amount)}
                      disabled={isLotteryPlaying}
                    >
                      {amount}
                    </button>
                  ))}
                </div>
              </div>
              <button
                className="buy-ticket-btn"
                onClick={playLottery}
                disabled={displayCredits < betAmount || lotteryNumbers.length !== 4 || isLotteryPlaying}
              >
                {isLotteryPlaying ? 'Drawing...' : 'Buy Ticket & Draw'}
              </button>
            </div>

            <button
              className="clear-selection-btn"
              onClick={() => {
                setLotteryNumbers([]);
                setWinningNumbers([]);
                setLotteryMatches(null);
                setLastWin(null);
              }}
              disabled={isLotteryPlaying}
            >
              Clear Selection
            </button>
          </div>
        </div>
      )}
    </div>
    {blackMarketButton}
  </div>
);

export default GamblingVenue;
