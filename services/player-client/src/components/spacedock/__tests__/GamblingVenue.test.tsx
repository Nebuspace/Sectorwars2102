// @vitest-environment jsdom
/**
 * GamblingVenue — menu / slots money-path controls (WO-TESTCOV-PLAYER-SPACEDOCK-SHELL).
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
    currentGame: 'menu' as GamblingGame,
    setCurrentGame: vi.fn(),
    betAmount: 10,
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

describe('GamblingVenue', () => {
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

  it('renders the four game cards on the menu', async () => {
    await act(async () => {
      root.render(<GamblingVenue {...baseProps()} />);
    });
    expect(container.textContent).toMatch(/FORTUNE FAVORS THE BOLD/);
    expect(container.querySelector('.game-card.slots')).toBeTruthy();
    expect(container.querySelector('.game-card.dice')).toBeTruthy();
    expect(container.querySelector('.game-card.blackjack')).toBeTruthy();
    expect(container.querySelector('.game-card.lottery')).toBeTruthy();
  });

  it('selects Cosmic Slots from the menu', async () => {
    const setCurrentGame = vi.fn();
    await act(async () => {
      root.render(<GamblingVenue {...baseProps({ setCurrentGame })} />);
    });
    await act(async () => {
      container.querySelector('.game-card.slots')!.dispatchEvent(
        new MouseEvent('click', { bubbles: true }),
      );
    });
    expect(setCurrentGame).toHaveBeenCalledWith('slots');
  });

  it('fires spinSlots from the SPIN button and disables when underfunded', async () => {
    const spinSlots = vi.fn();
    await act(async () => {
      root.render(
        <GamblingVenue
          {...baseProps({
            currentGame: 'slots',
            spinSlots,
            displayCredits: 5000,
            betAmount: 100,
          })}
        />,
      );
    });
    const spin = container.querySelector('button.spin-button') as HTMLButtonElement;
    expect(spin.disabled).toBe(false);
    await act(async () => {
      spin.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(spinSlots).toHaveBeenCalledTimes(1);

    await act(async () => {
      root.render(
        <GamblingVenue
          {...baseProps({
            currentGame: 'slots',
            spinSlots,
            displayCredits: 5,
            betAmount: 100,
          })}
        />,
      );
    });
    expect((container.querySelector('button.spin-button') as HTMLButtonElement).disabled).toBe(true);
  });

  it('surfaces gamblingError and routes Back correctly', async () => {
    const onBack = vi.fn();
    const setCurrentGame = vi.fn();
    const setLastWin = vi.fn();

    await act(async () => {
      root.render(
        <GamblingVenue
          {...baseProps({
            currentGame: 'slots',
            gamblingError: 'Spin failed',
            onBack,
            setCurrentGame,
            setLastWin,
          })}
        />,
      );
    });
    expect(container.querySelector('.gambling-error')?.textContent).toBe('Spin failed');

    await act(async () => {
      container.querySelector('button.back-button')!.dispatchEvent(
        new MouseEvent('click', { bubbles: true }),
      );
    });
    expect(setCurrentGame).toHaveBeenCalledWith('menu');
    expect(setLastWin).toHaveBeenCalledWith(null);
    expect(onBack).not.toHaveBeenCalled();

    await act(async () => {
      root.render(
        <GamblingVenue {...baseProps({ currentGame: 'menu', onBack, setCurrentGame })} />,
      );
    });
    await act(async () => {
      container.querySelector('button.back-button')!.dispatchEvent(
        new MouseEvent('click', { bubbles: true }),
      );
    });
    expect(onBack).toHaveBeenCalled();
  });
});
