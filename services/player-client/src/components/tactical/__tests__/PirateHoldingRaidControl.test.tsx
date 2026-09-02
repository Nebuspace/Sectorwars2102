// @vitest-environment jsdom
/**
 * PirateHoldingRaidControl — LEG-4107 pirate-holding raid initiate.
 */
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const listBySector = vi.fn();
const initiateRaid = vi.fn();
const refreshPlayerState = vi.fn();

let mockPlayerState: {
  id?: string;
  is_docked?: boolean;
  is_landed?: boolean;
} | null = { id: 'player-1', is_docked: false, is_landed: false };

let mockCurrentSector: { id: string; sector_id: number } | null = {
  id: '11111111-1111-1111-1111-111111111111',
  sector_id: 42,
};

vi.mock('../../../services/api', () => ({
  pirateHoldingsAPI: {
    listBySector: (...args: unknown[]) => listBySector(...args),
    initiateRaid: (...args: unknown[]) => initiateRaid(...args),
  },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    currentSector: mockCurrentSector,
    playerState: mockPlayerState,
    refreshPlayerState,
  }),
}));

import PirateHoldingRaidControl, {
  formatPirateHoldingRaidError,
} from '../PirateHoldingRaidControl';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const HOLDING_ID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatPirateHoldingRaidError (LEG-4107)', () => {
  const fallback = 'Pirate-holding raid initiate failed';

  it('densifies TypeError network collapse', () => {
    expect(formatPirateHoldingRaidError(new TypeError('Failed to fetch'), fallback)).toBe(
      fallback,
    );
  });

  it('surfaces 403 permission path without raw status codes', () => {
    expect(formatPirateHoldingRaidError(apiRequestError(403), fallback)).toMatch(/permission/i);
    expect(formatPirateHoldingRaidError(apiRequestError(403), fallback)).not.toMatch(/\b403\b/);
    expect(
      formatPirateHoldingRaidError(
        apiRequestError(403, 'Player must be in the holding anchor sector to initiate a raid'),
        fallback,
      ),
    ).toMatch(/anchor sector/i);
  });

  it('surfaces 429 rate-limit path without raw status codes', () => {
    expect(formatPirateHoldingRaidError(apiRequestError(429), fallback)).toMatch(/rate limit/i);
    expect(formatPirateHoldingRaidError(apiRequestError(429), fallback)).not.toMatch(/\b429\b/);
    expect(formatPirateHoldingRaidError(apiRequestError(429), fallback)).not.toMatch(/TypeError/i);
  });
});

describe('PirateHoldingRaidControl', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    listBySector.mockReset();
    initiateRaid.mockReset();
    refreshPlayerState.mockReset();
    refreshPlayerState.mockResolvedValue(undefined);
    mockPlayerState = { id: 'player-1', is_docked: false, is_landed: false };
    mockCurrentSector = {
      id: '11111111-1111-1111-1111-111111111111',
      sector_id: 42,
    };
    listBySector.mockResolvedValue([
      { id: HOLDING_ID, tier: 'OUTPOST', sector_id: 42 },
    ]);
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

  it('shows initiate action when holdings are present', async () => {
    await act(async () => {
      root.render(<PirateHoldingRaidControl />);
      await flush();
    });
    expect(listBySector).toHaveBeenCalledWith(42);
    expect(
      container.querySelector(`[data-testid="pirate-holding-raid-initiate-${HOLDING_ID}"]`),
    ).toBeTruthy();
    expect(container.textContent).toMatch(/pirate holding/i);
    expect(container.textContent).toMatch(/Outpost/i);
  });

  it('calls initiateRaid and refreshes state on success', async () => {
    initiateRaid.mockResolvedValue({
      holding_id: HOLDING_ID,
      tier: 'OUTPOST',
      initiated: true,
      lock_applied: true,
      combat_lock_held_by: 'player-1',
      combat_lock_team_snapshot: ['player-1'],
    });
    listBySector
      .mockResolvedValueOnce([{ id: HOLDING_ID, tier: 'OUTPOST', sector_id: 42 }])
      .mockResolvedValueOnce([{ id: HOLDING_ID, tier: 'OUTPOST', sector_id: 42 }]);

    await act(async () => {
      root.render(<PirateHoldingRaidControl />);
      await flush();
    });

    const btn = container.querySelector(
      `[data-testid="pirate-holding-raid-initiate-${HOLDING_ID}"]`,
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
    });

    expect(initiateRaid).toHaveBeenCalledWith(HOLDING_ID);
    expect(refreshPlayerState).toHaveBeenCalled();
    expect(container.querySelector('[data-testid="pirate-holding-raid-msg"]')?.textContent).toMatch(
      /Raid initiated/i,
    );
  });

  it('surfaces 403 initiate errors with player-safe copy', async () => {
    initiateRaid.mockRejectedValue(apiRequestError(403));

    await act(async () => {
      root.render(<PirateHoldingRaidControl />);
      await flush();
    });

    const btn = container.querySelector(
      `[data-testid="pirate-holding-raid-initiate-${HOLDING_ID}"]`,
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
    });

    const text = container.querySelector('[data-testid="pirate-holding-raid-msg"]')?.textContent;
    expect(text).toMatch(/permission/i);
    expect(text).not.toMatch(/\b403\b/);
  });

  it('surfaces 429 initiate errors with player-safe copy', async () => {
    initiateRaid.mockRejectedValue(apiRequestError(429));

    await act(async () => {
      root.render(<PirateHoldingRaidControl />);
      await flush();
    });

    const btn = container.querySelector(
      `[data-testid="pirate-holding-raid-initiate-${HOLDING_ID}"]`,
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
    });

    const text = container.querySelector('[data-testid="pirate-holding-raid-msg"]')?.textContent;
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/\b429\b/);
  });
});
