// @vitest-environment jsdom
/**
 * GamblingVenue — Nebula Dice controls (WO-TESTCOV-PLAYER-GAMBLING-DICE).
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
    currentGame: 'dice' as GamblingGame,
    setCurrentGame: vi.fn(),
    betAmount: 100,
    setBetAmount: vi.fn(),
    slotReels: ['⭐', '🚀', '🌍'],
    isSpinning: false,
    isJackpot: false,
    lastWin: null as number | null,
    setLastWin: vi.fn(),
    spinSlots: vi.fn(),
    diceValues: [0, 0],
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
    lotteryNumbers: [] as number[],
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

describe('GamblingVenue — Nebula Dice', () => {
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

  it('ROLL THE DICE calls rollDice when funded', async () => {
    const rollDice = vi.fn<() => void>();
    await act(async () => {
      root.render(<GamblingVenue {...baseProps({ rollDice })} />);
    });
    const btn = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('ROLL THE DICE'),
    ) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    await act(async () => {
      btn.click();
    });
    expect(rollDice).toHaveBeenCalled();
  });

  it('disables ROLL when underfunded', async () => {
    await act(async () => {
      root.render(<GamblingVenue {...baseProps({ displayCredits: 10, betAmount: 100 })} />);
    });
    const btn = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('ROLL THE DICE'),
    ) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });
});
