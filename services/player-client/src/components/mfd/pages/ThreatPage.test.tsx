// @vitest-environment jsdom
/**
 * ThreatPage — LAW STATUS block (WO-UIPC-LAW-GREY-STATUS).
 *
 * Mirrors SalvagePage.test.tsx's seam: jsdom + react-dom/client
 * createRoot + act(), no RTL. Fake timers pin Date.now() so the countdown
 * (greyUntil - now) is deterministic instead of racing real wall-clock ms.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const mockGetStatus = vi.fn();
const mockClearFine = vi.fn();

vi.mock('../../../services/api', () => ({
  greyStatusAPI: {
    getStatus: (...a: unknown[]) => mockGetStatus(...a),
    clearFine: (...a: unknown[]) => mockClearFine(...a),
  },
}));

const CURRENT_SECTOR = {
  id: 'sector-uuid', sector_id: 5, name: 'Test Sector', type: 'STANDARD',
  hazard_level: 0, radiation_level: 0, resources: {}, players_present: [],
};

const PLAYER_STATE = {
  mines: 0, is_docked: true, is_landed: false, credits: 5000,
};

const mockDeployMines = vi.fn();
const mockUpdatePlayerCredits = vi.fn();

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    currentSector: CURRENT_SECTOR,
    playerState: PLAYER_STATE,
    deployMines: mockDeployMines,
    updatePlayerCredits: mockUpdatePlayerCredits,
  }),
}));

import ThreatPage from './ThreatPage';

const NOW = new Date('2026-07-10T12:00:00Z').getTime();

describe('ThreatPage — LAW STATUS', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetStatus.mockReset();
    mockClearFine.mockReset();
    mockDeployMines.mockReset();
    mockUpdatePlayerCredits.mockReset();
    vi.useFakeTimers();
    vi.setSystemTime(NOW);

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
    vi.useRealTimers();
  });

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  const click = async (el: Element) => {
    await act(async () => {
      el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
  };

  const mount = async () => {
    await act(async () => {
      root.render(<ThreatPage />);
    });
    await flush();
  };

  it('shows GOOD STANDING and no CLEAR FINE button when the player is not grey', async () => {
    mockGetStatus.mockResolvedValue({
      isGrey: false, kind: null, greyUntil: null, remainingSeconds: 0, clearFineCredits: null,
    });

    await mount();

    expect(container.querySelector('.mfd-law-clean')?.textContent).toBe(
      'GOOD STANDING — no active grey flag.'
    );
    expect(container.querySelector('.mfd-law-btn')).toBeNull();
  });

  it('renders the GREY status, kind label, live countdown, and CLEAR FINE button when grey', async () => {
    mockGetStatus.mockResolvedValue({
      isGrey: true,
      kind: 'player_attack',
      greyUntil: new Date(NOW + 90_000).toISOString(), // +90s
      remainingSeconds: 90,
      clearFineCredits: 250,
    });

    await mount();

    const warn = container.querySelector('.mfd-page-warnline');
    expect(warn).not.toBeNull();
    expect(warn!.textContent).toContain('Attacked a lawful player');
    expect(warn!.textContent).toContain('1m 30s');

    const btn = container.querySelector('.mfd-law-btn');
    expect(btn).not.toBeNull();
    expect(btn!.textContent).toBe('Clear Fine (₡250)');
  });

  it('ticks the countdown down as fake time advances', async () => {
    mockGetStatus.mockResolvedValue({
      isGrey: true,
      kind: 'station_attack',
      greyUntil: new Date(NOW + 65_000).toISOString(),
      remainingSeconds: 65,
      clearFineCredits: 1000,
    });

    await mount();
    expect(container.querySelector('.mfd-page-warnline')!.textContent).toContain('1m 05s');

    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });

    expect(container.querySelector('.mfd-page-warnline')!.textContent).toContain('55s');
  });

  it('CLEAR FINE success feeds the new balance to updatePlayerCredits and refetches (clean state)', async () => {
    mockGetStatus
      .mockResolvedValueOnce({
        isGrey: true, kind: 'player_attack',
        greyUntil: new Date(NOW + 90_000).toISOString(), remainingSeconds: 90, clearFineCredits: 250,
      })
      .mockResolvedValueOnce({
        isGrey: false, kind: null, greyUntil: null, remainingSeconds: 0, clearFineCredits: null,
      });
    mockClearFine.mockResolvedValue({
      success: true, message: 'Fine paid.', finePaid: 250, creditsRemaining: 4750,
    });

    await mount();
    await click(container.querySelector('.mfd-law-btn')!);
    await flush();

    expect(mockClearFine).toHaveBeenCalledTimes(1);
    expect(mockUpdatePlayerCredits).toHaveBeenCalledWith(4750);
    expect(mockGetStatus).toHaveBeenCalledTimes(2);
    expect(container.querySelector('.mfd-law-clean')?.textContent).toBe(
      'GOOD STANDING — no active grey flag.'
    );
  });

  it('CLEAR FINE failure (success:false) shows the server message and does not touch credits', async () => {
    mockGetStatus.mockResolvedValue({
      isGrey: true, kind: 'player_attack',
      greyUntil: new Date(NOW + 90_000).toISOString(), remainingSeconds: 90, clearFineCredits: 250,
    });
    mockClearFine.mockResolvedValue({
      success: false, message: 'Insufficient credits', finePaid: null, creditsRemaining: null,
    });

    await mount();
    await click(container.querySelector('.mfd-law-btn')!);
    await flush();

    expect(mockUpdatePlayerCredits).not.toHaveBeenCalled();
    expect(container.querySelector('.mfd-law-msg.err')?.textContent).toBe('Insufficient credits');
    // Still grey -- the button remains reachable to retry.
    expect(container.querySelector('.mfd-law-btn')).not.toBeNull();
  });

  it('a failed grey-status fetch shows an error line instead of crashing the page', async () => {
    mockGetStatus.mockRejectedValue(new Error('Network down'));

    await mount();

    expect(container.querySelector('.mfd-page-section .mfd-page-warnline')?.textContent).toBe(
      'Network down'
    );
    expect(container.querySelector('.mfd-law-clean')).toBeNull();
    expect(container.querySelector('.mfd-law-btn')).toBeNull();
  });
});
