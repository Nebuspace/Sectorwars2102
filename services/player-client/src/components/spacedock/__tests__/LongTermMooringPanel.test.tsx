// @vitest-environment jsdom
/**
 * LEG-438 — LongTermMooringPanel: acquire / release / capacity denial.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockAcquire = vi.fn();
const mockRelease = vi.fn();
const mockRefresh = vi.fn().mockResolvedValue(undefined);
const mockUpdateCredits = vi.fn();

let mockPlayerState: {
  is_docked: boolean;
  current_port_id?: string;
  credits: number;
} = {
  is_docked: true,
  current_port_id: 'station-1',
  credits: 50_000,
};

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: mockPlayerState,
    stationsInSector: [{ id: 'station-1', name: 'Test Port' }],
    refreshPlayerState: (...a: unknown[]) => mockRefresh(...a),
    updatePlayerCredits: (...a: unknown[]) => mockUpdateCredits(...a),
  }),
}));

vi.mock('../../../services/api', () => ({
  tradingAPI: {
    acquireLongTermMooring: (...a: unknown[]) => mockAcquire(...a),
    releaseLongTermMooring: (...a: unknown[]) => mockRelease(...a),
  },
}));

import LongTermMooringPanel, {
  LONG_TERM_MOORING_RATE_PER_DAY,
} from '../LongTermMooringPanel';

describe('LongTermMooringPanel (LEG-438)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockAcquire.mockReset();
    mockRelease.mockReset();
    mockRefresh.mockReset().mockResolvedValue(undefined);
    mockUpdateCredits.mockReset();
    mockPlayerState = {
      is_docked: true,
      current_port_id: 'station-1',
      credits: 50_000,
    };
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('happy path: acquire posts tip days + station_id and surfaces success', async () => {
    mockAcquire.mockResolvedValue({
      message: 'Long-term mooring secured at Test Port for 7 day(s)',
      days: 7,
      fee_paid: 1400,
      credits_remaining: 48600,
    });

    await act(async () => {
      root.render(<LongTermMooringPanel />);
    });

    expect(container.querySelector('[data-testid="mooring-fee-preview"]')?.textContent).toMatch(
      /1[,.]?400/,
    );

    const acquireBtn = container.querySelector(
      '[data-testid="mooring-acquire"]',
    ) as HTMLButtonElement;
    await act(async () => {
      acquireBtn.click();
    });

    expect(mockAcquire).toHaveBeenCalledWith('station-1', 7);
    expect(mockUpdateCredits).toHaveBeenCalledWith(48600);
    expect(container.querySelector('[data-testid="mooring-success"]')?.textContent).toMatch(
      /secured/i,
    );
  });

  it('happy path: release calls tip release endpoint', async () => {
    mockRelease.mockResolvedValue({
      message: 'Long-term mooring released',
      released: true,
    });

    await act(async () => {
      root.render(<LongTermMooringPanel />);
    });

    const releaseBtn = container.querySelector(
      '[data-testid="mooring-release"]',
    ) as HTMLButtonElement;
    await act(async () => {
      releaseBtn.click();
    });

    expect(mockRelease).toHaveBeenCalledTimes(1);
    expect(mockRefresh).toHaveBeenCalled();
    expect(container.querySelector('[data-testid="mooring-success"]')?.textContent).toMatch(
      /released/i,
    );
  });

  it('capacity denial: 409 surfaces occupied/capacity honestly', async () => {
    const err = new Error('All long-term mooring slips at Test Port are occupied (3/3)');
    (err as any).status = 409;
    (err as any).data = {
      detail: 'All long-term mooring slips at Test Port are occupied (3/3)',
      slips: { capacity: 3, occupied: 3 },
    };
    mockAcquire.mockRejectedValue(err);

    await act(async () => {
      root.render(<LongTermMooringPanel />);
    });

    await act(async () => {
      (container.querySelector('[data-testid="mooring-acquire"]') as HTMLButtonElement).click();
    });

    const alert = container.querySelector('[data-testid="mooring-error"]');
    expect(alert?.textContent).toMatch(/occupied/i);
    expect(alert?.textContent).toMatch(/3\/3/);
  });

  it('undocked: shows hint and disables acquire', async () => {
    mockPlayerState = { is_docked: false, credits: 50_000 };

    await act(async () => {
      root.render(<LongTermMooringPanel />);
    });

    expect(container.querySelector('[data-testid="mooring-undocked-hint"]')).toBeTruthy();
    expect(
      (container.querySelector('[data-testid="mooring-acquire"]') as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});
