// @vitest-environment jsdom
/**
 * GamblingVenue — Sector Lottery ticket controls (WO-TESTCOV-PLAYER-GAMBLING-LOTTERY).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import GamblingVenue, { type GamblingGame } from '../GamblingVenue';

function baseProps(overrides: Record<string, unknown> = {}) {
  return {
    onBack: vi.fn(),
    displayCredits: 5000,
    gamblingError: null as string | null,
    currentGame: 'lottery' as GamblingGame,
    setCurrentGame: vi.fn(),
    betAmount: 100,
    setBetAmount: vi.fn(),
    slotReels: ['⭐', '🚀', '🌍'],
    isSpinning: false,
    isJackpot: false,
    lastWin: null as number | null,
    setLastWin: vi.fn(),
    spinSlots: vi.fn(),
    diceValues: [1, 2],
    diceBetType: 'high' as const,
    setDiceBetType: vi.fn(),
    diceExactBet: 7,
    setDiceExactBet: vi.fn(),
    isSupernova: false,
    isVoid: false,
    rollDice: vi.fn(),
    blackjackGame: null,
    setBlackjackGame: vi.fn(),
    isBlackjackDealing: false,
    dealBlackjack: vi.fn(),
    blackjackAction: vi.fn(),
    lotteryNumbers: [1, 2, 3, 4] as number[],
    setLotteryNumbers: vi.fn(),
    winningNumbers: [] as number[],
    setWinningNumbers: vi.fn(),
    lotteryMatches: null as number | null,
    setLotteryMatches: vi.fn(),
    isLotteryPlaying: false,
    toggleLotteryNumber: vi.fn(),
    playLottery: vi.fn(),
    blackMarketButton: null,
    ...overrides,
  };
}

describe('GamblingVenue — Sector Lottery', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('BUY TICKET calls playLottery when 4 numbers are selected', async () => {
    const playLottery = vi.fn<() => void>();
    await act(async () => {
      root.render(<GamblingVenue {...baseProps({ playLottery })} />);
    });
    const btn = container.querySelector('.buy-ticket-btn') as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    await act(async () => {
      btn.click();
    });
    expect(playLottery).toHaveBeenCalled();
  });

  it('disables BUY TICKET until 4 numbers are selected', async () => {
    await act(async () => {
      root.render(<GamblingVenue {...baseProps({ lotteryNumbers: [1, 2] })} />);
    });
    expect((container.querySelector('.buy-ticket-btn') as HTMLButtonElement).disabled).toBe(true);
  });
});
