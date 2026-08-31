// @vitest-environment jsdom
/**
 * SectorRetreatControl — LEG-3107 sector flee (POST /combat/retreat).
 */
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const retreatFromSector = vi.fn();
const refreshPlayerState = vi.fn();
const getAvailableMoves = vi.fn();

let mockPlayerState: {
  is_docked?: boolean;
  is_landed?: boolean;
} | null = { is_docked: false, is_landed: false };

vi.mock('../../../services/api', () => ({
  combatAPI: {
    retreatFromSector: (...args: unknown[]) => retreatFromSector(...args),
  },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: mockPlayerState,
    refreshPlayerState,
    getAvailableMoves,
  }),
}));

import SectorRetreatControl from '../SectorRetreatControl';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('SectorRetreatControl', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    retreatFromSector.mockReset();
    refreshPlayerState.mockReset();
    getAvailableMoves.mockReset();
    refreshPlayerState.mockResolvedValue(undefined);
    getAvailableMoves.mockResolvedValue(undefined);
    mockPlayerState = { is_docked: false, is_landed: false };
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it('disables flee when docked', async () => {
    mockPlayerState = { is_docked: true, is_landed: false };
    await act(async () => {
      root.render(<SectorRetreatControl />);
    });
    expect(container.querySelector('[data-testid="sector-retreat-flee"]')).toBeNull();
    expect(container.textContent).toMatch(/Cannot flee while docked/);
  });

  it('disables flee when landed', async () => {
    mockPlayerState = { is_docked: false, is_landed: true };
    await act(async () => {
      root.render(<SectorRetreatControl />);
    });
    expect(container.querySelector('[data-testid="sector-retreat-flee"]')).toBeNull();
    expect(container.textContent).toMatch(/Cannot flee while landed/);
  });

  it('calls retreatFromSector and refreshes state on success', async () => {
    retreatFromSector.mockResolvedValue({
      success: true,
      message: 'You escaped to sector 42!',
      newSectorId: 42,
      turnsConsumed: 3,
      turnsRemaining: 97,
    });
    await act(async () => {
      root.render(<SectorRetreatControl />);
    });
    const btn = container.querySelector('[data-testid="sector-retreat-flee"]') as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
    });
    expect(retreatFromSector).toHaveBeenCalledTimes(1);
    expect(refreshPlayerState).toHaveBeenCalled();
    expect(getAvailableMoves).toHaveBeenCalled();
    expect(container.querySelector('[data-testid="sector-retreat-msg"]')?.textContent).toMatch(
      /escaped to sector 42/i,
    );
  });

  it('surfaces failure message from GS', async () => {
    retreatFromSector.mockResolvedValue({
      success: false,
      message: 'Escape failed — guards blocked the warp.',
      escapeChance: 35,
      turnsConsumed: 3,
      turnsRemaining: 97,
    });
    await act(async () => {
      root.render(<SectorRetreatControl />);
    });
    const btn = container.querySelector('[data-testid="sector-retreat-flee"]') as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
    });
    const msg = container.querySelector('[data-testid="sector-retreat-msg"]');
    expect(msg?.textContent).toMatch(/Escape failed/);
    expect(msg?.textContent).toMatch(/35%/);
    expect(refreshPlayerState).toHaveBeenCalled();
  });

  it('surfaces API errors honestly', async () => {
    retreatFromSector.mockRejectedValue(new Error('Not enough turns'));
    await act(async () => {
      root.render(<SectorRetreatControl />);
    });
    const btn = container.querySelector('[data-testid="sector-retreat-flee"]') as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
    });
    expect(container.querySelector('[data-testid="sector-retreat-msg"]')?.textContent).toMatch(
      /Not enough turns/,
    );
  });
});
