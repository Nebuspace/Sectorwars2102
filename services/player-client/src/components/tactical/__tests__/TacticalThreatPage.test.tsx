// @vitest-environment jsdom
/**
 * TacticalThreatPage — TACTICAL monitor's THREAT page (WO-UI2-DECK-
 * RECONCILE, §05: "law status → CLEAR FINE · mines → LAY 5 · hazard
 * readout"). Ported from mfd/pages/ThreatPage.tsx's logic (own hooks,
 * same law/mine handlers) -- mirrors ThreatPage.test.tsx's own harness.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockGetStatus = vi.fn();
const mockClearFine = vi.fn();
vi.mock('../../../services/api', () => ({
  greyStatusAPI: {
    getStatus: (...a: unknown[]) => mockGetStatus(...a),
    clearFine: (...a: unknown[]) => mockClearFine(...a),
  },
}));

let gameState: any;
const mockDeployMines = vi.fn();
const mockUpdatePlayerCredits = vi.fn();
vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => gameState,
}));

import TacticalThreatPage from '../pages/TacticalThreatPage';

describe('TacticalThreatPage', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetStatus.mockReset();
    mockClearFine.mockReset();
    mockDeployMines.mockReset();
    mockUpdatePlayerCredits.mockReset();
    mockGetStatus.mockResolvedValue({ isGrey: false, kind: null, greyUntil: null, remainingSeconds: 0, clearFineCredits: null });

    gameState = {
      currentSector: { hazard_level: 4, radiation_level: 0.12, type: 'nebula' },
      playerState: { mines: 3, is_docked: false, is_landed: false },
      deployMines: mockDeployMines,
      updatePlayerCredits: mockUpdatePlayerCredits,
    };

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
    await flush();
  };

  const mount = async () => {
    await act(async () => {
      root.render(<TacticalThreatPage />);
    });
    await flush();
  };

  it('shows GOOD STANDING when not grey-flagged, announced as a status', async () => {
    await mount();
    const clean = container.querySelector('.threat-law-clean')!;
    expect(clean.textContent).toContain('GOOD STANDING');
    expect(clean.getAttribute('role')).toBe('status');
  });

  it('shows the grey-flag warning + CLEAR FINE when grey, the countdown as aria-live=polite (not alert)', async () => {
    mockGetStatus.mockResolvedValue({
      isGrey: true, kind: 'player_attack', greyUntil: new Date(Date.now() + 60000).toISOString(),
      remainingSeconds: 60, clearFineCredits: 250,
    });
    await mount();

    const warnline = container.querySelector('.threat-warnline')!;
    expect(warnline.textContent).toContain('GREY');
    // Frequent 1s-tick update -- polite, not alert (that role is reserved
    // for the load-error branch; Pixel a11y gate, WO-UI2-DECK-RECONCILE).
    expect(warnline.getAttribute('aria-live')).toBe('polite');
    expect(warnline.getAttribute('role')).not.toBe('alert');
    const btn = container.querySelector('.threat-btn')!;
    expect(btn.textContent).toContain('CLEAR FINE');
  });

  it('a failed grey-status load is announced as role=alert (assertive)', async () => {
    mockGetStatus.mockRejectedValue(new Error('Failed to load law status'));
    await mount();

    const warnline = container.querySelector('.threat-warnline')!;
    expect(warnline.textContent).toBe('Failed to load law status');
    expect(warnline.getAttribute('role')).toBe('alert');
  });

  it('the LOADING… transient state is announced as role=status', async () => {
    let resolveStatus: (v: any) => void;
    mockGetStatus.mockImplementation(() => new Promise((resolve) => { resolveStatus = resolve; }));

    await act(async () => {
      root.render(<TacticalThreatPage />);
    });
    await flush();

    const loading = container.querySelector('.empty-state')!;
    expect(loading.textContent).toBe('LOADING…');
    expect(loading.getAttribute('role')).toBe('status');

    await act(async () => {
      resolveStatus({ isGrey: false, kind: null, greyUntil: null, remainingSeconds: 0, clearFineCredits: null });
    });
  });

  it('CLEAR FINE calls greyStatusAPI.clearFine, sets aria-busy while pending, and updates credits on success', async () => {
    mockGetStatus.mockResolvedValue({
      isGrey: true, kind: 'player_attack', greyUntil: new Date(Date.now() + 60000).toISOString(),
      remainingSeconds: 60, clearFineCredits: 250,
    });
    let resolveClearFine: (v: any) => void;
    mockClearFine.mockImplementation(() => new Promise((resolve) => { resolveClearFine = resolve; }));
    await mount();

    const btn = container.querySelector('.threat-btn')!;
    expect(btn.getAttribute('aria-busy')).toBe('false');
    await act(async () => {
      btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(container.querySelector('.threat-btn')?.getAttribute('aria-busy')).toBe('true');

    await act(async () => {
      resolveClearFine({ success: true, message: null, finePaid: 250, creditsRemaining: 750 });
    });
    await flush();

    expect(mockClearFine).toHaveBeenCalled();
    expect(mockUpdatePlayerCredits).toHaveBeenCalledWith(750);
    expect(container.querySelector('.threat-msg')?.getAttribute('role')).toBe('status');
  });

  it('LAY 5 deploys mines when carrying mines in open space, result announced as a status', async () => {
    mockDeployMines.mockResolvedValue({ message: 'Deployed 1 mine(s).' });
    await mount();

    const btn = container.querySelector('.threat-btn')!;
    expect(btn.textContent).toContain('LAY 5');
    expect(btn.getAttribute('aria-busy')).toBe('false');
    await click(btn);

    expect(mockDeployMines).toHaveBeenCalled();
    const msg = container.querySelector('.threat-msg.ok')!;
    expect(msg.textContent).toContain('Deployed');
    expect(msg.getAttribute('role')).toBe('status');
  });

  it('shows a hint instead of the mine control when docked (not open space)', async () => {
    gameState.playerState.is_docked = true;
    await mount();
    expect(container.querySelector('.threat-hint')?.textContent).toContain('Undock');
    expect(container.querySelector('.threat-mine-input')).toBeNull();
  });

  it('shows a hint instead of the mine control with zero mines carried', async () => {
    gameState.playerState.mines = 0;
    await mount();
    expect(container.querySelector('.threat-hint')?.textContent).toContain('No mines aboard');
  });

  it('renders the hazard readout (hazard/radiation/sector type)', async () => {
    await mount();
    const text = container.textContent || '';
    expect(text).toContain('HAZARD READOUT');
    expect(container.querySelector('.hud-value.danger')?.textContent).toContain('4/10');
    expect(text).toContain('12.0%');
    expect(text).toContain('NEBULA');
  });

  it('marks the three section titles as headings (role=heading aria-level=3), not raw <h3>', async () => {
    await mount();
    const titles = Array.from(container.querySelectorAll('.threat-section-title'));
    expect(titles.map((t) => t.textContent?.trim().replace(/\s+/g, ' '))).toEqual([
      'LAW STATUS', 'MINES ABOARD 3', 'HAZARD READOUT',
    ]);
    titles.forEach((t) => {
      expect(t.tagName.toLowerCase()).not.toBe('h3');
      expect(t.getAttribute('role')).toBe('heading');
      expect(t.getAttribute('aria-level')).toBe('3');
    });
  });

  it('shows an empty state with no sector/player telemetry, announced as a status', async () => {
    gameState.currentSector = null;
    gameState.playerState = null;
    await mount();
    const empty = container.querySelector('.empty-state')!;
    expect(empty.textContent).toBe('No sector telemetry');
    expect(empty.getAttribute('role')).toBe('status');
  });
});
