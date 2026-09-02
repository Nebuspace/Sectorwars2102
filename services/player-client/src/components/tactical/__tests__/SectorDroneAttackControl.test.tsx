// @vitest-environment jsdom
/**
 * SectorDroneAttackControl — LEG-3968 sector drone combat.
 */
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const attackSectorDrones = vi.fn();
const getSectorDrones = vi.fn();
const refreshPlayerState = vi.fn();
const getAvailableMoves = vi.fn();

let mockPlayerState: {
  id?: string;
  is_docked?: boolean;
  is_landed?: boolean;
} | null = { id: 'player-1', is_docked: false, is_landed: false };

let mockCurrentSector: { id: string } | null = {
  id: '11111111-1111-1111-1111-111111111111',
};

vi.mock('../../../services/api', () => ({
  combatAPI: {
    attackSectorDrones: (...args: unknown[]) => attackSectorDrones(...args),
  },
  droneFleetAPI: {
    getSectorDrones: (...args: unknown[]) => getSectorDrones(...args),
  },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    currentSector: mockCurrentSector,
    playerState: mockPlayerState,
    refreshPlayerState,
    getAvailableMoves,
  }),
}));

import SectorDroneAttackControl, { formatSectorDroneAttackError } from '../SectorDroneAttackControl';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatSectorDroneAttackError (LEG-3968)', () => {
  const fallback = 'Sector drone attack failed';

  it('densifies TypeError network collapse', () => {
    expect(formatSectorDroneAttackError(new TypeError('Failed to fetch'), fallback)).toBe(fallback);
  });

  it('surfaces 403/429 status paths without raw status codes', () => {
    expect(formatSectorDroneAttackError(apiRequestError(403), fallback)).toMatch(/permission/i);
    expect(formatSectorDroneAttackError(apiRequestError(429), fallback)).toMatch(/rate limit/i);
    expect(formatSectorDroneAttackError(apiRequestError(429), fallback)).not.toMatch(/\b429\b/);
  });
});

describe('SectorDroneAttackControl', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    attackSectorDrones.mockReset();
    getSectorDrones.mockReset();
    refreshPlayerState.mockReset();
    getAvailableMoves.mockReset();
    refreshPlayerState.mockResolvedValue(undefined);
    getAvailableMoves.mockResolvedValue(undefined);
    mockPlayerState = { id: 'player-1', is_docked: false, is_landed: false };
    mockCurrentSector = { id: '11111111-1111-1111-1111-111111111111' };
    getSectorDrones.mockResolvedValue([
      { player_id: 'other-player', status: 'deployed', health: 10 },
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

  it('shows clear action when hostile drones are present', async () => {
    await act(async () => {
      root.render(<SectorDroneAttackControl />);
      await flush();
    });
    expect(container.querySelector('[data-testid="sector-drone-attack-clear"]')).toBeTruthy();
    expect(container.textContent).toMatch(/hostile drone/i);
  });

  it('calls attackSectorDrones and refreshes state on success', async () => {
    attackSectorDrones.mockResolvedValue({
      success: true,
      message: 'Sector cleared.',
      dronesDestroyed: 1,
      turnsConsumed: 2,
      turnsRemaining: 8,
    });
    getSectorDrones
      .mockResolvedValueOnce([{ player_id: 'other-player', status: 'deployed', health: 10 }])
      .mockResolvedValueOnce([]);

    await act(async () => {
      root.render(<SectorDroneAttackControl />);
      await flush();
    });

    const btn = container.querySelector('[data-testid="sector-drone-attack-clear"]') as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
    });

    expect(attackSectorDrones).toHaveBeenCalledTimes(1);
    expect(refreshPlayerState).toHaveBeenCalled();
    expect(getAvailableMoves).toHaveBeenCalled();
    expect(container.querySelector('[data-testid="sector-drone-attack-msg"]')?.textContent).toMatch(
      /Sector cleared/i,
    );
  });

  it('surfaces API errors honestly', async () => {
    attackSectorDrones.mockRejectedValue(new Error('No hostile drones present in this sector'));

    await act(async () => {
      root.render(<SectorDroneAttackControl />);
      await flush();
    });

    const btn = container.querySelector('[data-testid="sector-drone-attack-clear"]') as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
    });

    expect(container.querySelector('[data-testid="sector-drone-attack-msg"]')?.textContent).toMatch(
      /No hostile drones/i,
    );
  });
});
