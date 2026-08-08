// @vitest-environment jsdom
/**
 * TractorLockPrompt — WO-WIRE-TRACTOR-LOCK-SURRENDER-UI.
 * Pins Break free → POST .../tractor-lock/break and Surrender → POST .../surrender.
 */
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const breakTractorLock = vi.fn();
const surrenderTractorLock = vi.fn();
const clearTractorLock = vi.fn();
const refreshPlayerState = vi.fn().mockResolvedValue(undefined);

let tractorLock: {
  station_id: string;
  ship_id: string;
  tractor_strength: string;
  reason: string;
  break_attempt_cost: string;
} | null = {
  station_id: 'st-1',
  ship_id: 'ship-1',
  tractor_strength: 'moderate',
  reason: 'stolen_ship',
  break_attempt_cost: '50-pct engine + 3 turns',
};

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    get tractorLock() {
      return tractorLock;
    },
    clearTractorLock,
    refreshPlayerState,
  }),
}));

vi.mock('../../../services/api', () => ({
  stationSecurityAPI: {
    breakTractorLock: (...args: unknown[]) => breakTractorLock(...args),
    surrenderTractorLock: (...args: unknown[]) => surrenderTractorLock(...args),
  },
}));

import TractorLockPrompt from '../TractorLockPrompt';

describe('TractorLockPrompt', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    breakTractorLock.mockReset();
    surrenderTractorLock.mockReset();
    clearTractorLock.mockReset();
    refreshPlayerState.mockClear();
    tractorLock = {
      station_id: 'st-1',
      ship_id: 'ship-1',
      tractor_strength: 'moderate',
      reason: 'stolen_ship',
      break_attempt_cost: '50-pct engine + 3 turns',
    };
    window.confirm = vi.fn(() => true);
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

  it('renders nothing when tractorLock is null', async () => {
    tractorLock = null;
    await act(async () => {
      root.render(<TractorLockPrompt />);
    });
    expect(container.querySelector('[data-testid="tractor-lock-prompt"]')).toBeNull();
  });

  it('calls break API and clears lock on escape', async () => {
    breakTractorLock.mockResolvedValue({ success: true, outcome: 'escaped' });

    await act(async () => {
      root.render(<TractorLockPrompt />);
    });

    const btn = container.querySelector(
      '[data-testid="tractor-lock-break"]'
    ) as HTMLButtonElement;
    expect(btn).not.toBeNull();

    await act(async () => {
      btn.click();
    });

    expect(breakTractorLock).toHaveBeenCalledWith('st-1');
    expect(clearTractorLock).toHaveBeenCalled();
    expect(refreshPlayerState).toHaveBeenCalled();
  });

  it('calls surrender API after confirm', async () => {
    surrenderTractorLock.mockResolvedValue({ surrendered: true });

    await act(async () => {
      root.render(<TractorLockPrompt />);
    });

    const btn = container.querySelector(
      '[data-testid="tractor-lock-surrender"]'
    ) as HTMLButtonElement;

    await act(async () => {
      btn.click();
    });

    expect(window.confirm).toHaveBeenCalled();
    expect(surrenderTractorLock).toHaveBeenCalledWith('st-1');
    expect(clearTractorLock).toHaveBeenCalled();
  });
});
