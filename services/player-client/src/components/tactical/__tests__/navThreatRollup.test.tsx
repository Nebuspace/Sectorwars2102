// @vitest-environment jsdom
/**
 * LEG-3143 — GET /nav/threat consumer: band render, empty graph, TypeError fallback.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockGetThreat = vi.fn();
vi.mock('../../../services/api', () => ({
  greyStatusAPI: {
    getStatus: vi.fn().mockResolvedValue({
      isGrey: false,
      kind: null,
      greyUntil: null,
      remainingSeconds: 0,
      clearFineCredits: null,
    }),
    clearFine: vi.fn(),
  },
  armoryAPI: {
    getCatalog: vi.fn().mockResolvedValue({ loadout: { mines: 0, limpet_mines: 0 } }),
  },
  navAPI: {
    getThreat: (...a: unknown[]) => mockGetThreat(...a),
  },
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ lastLimpetSignal: null, limpetSignalEventSignal: 0 }),
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    currentSector: { sector_id: 42, hazard_level: 1, radiation_level: 0, type: 'normal' },
    playerState: { mines: 0, is_docked: false, is_landed: false },
    deployMines: vi.fn(),
    updatePlayerCredits: vi.fn(),
  }),
}));

import TacticalThreatPage from '../pages/TacticalThreatPage';

describe('nav threat rollup on TacticalThreatPage (LEG-3143)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetThreat.mockReset();
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

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  const mount = async () => {
    await act(async () => {
      root.render(<TacticalThreatPage />);
    });
    await flush();
  };

  it('renders threat bands from GET /nav/threat', async () => {
    mockGetThreat.mockResolvedValue([
      { sector_id: 42, score: 12, band: 'CAUTION', contributors: [{ input: 'mines', points: 8 }] },
      { sector_id: 99, score: 40, band: 'HOSTILE', contributors: [] },
    ]);
    await mount();
    expect(container.textContent).toContain('NAV THREAT ROLLUP');
    expect(container.textContent).toContain('CAUTION');
    expect(container.textContent).toContain('HOSTILE');
    expect(container.querySelector('.nav-threat-hostile')).toBeTruthy();
  });

  it('shows empty state when threat graph is empty', async () => {
    mockGetThreat.mockResolvedValue([]);
    await mount();
    const empty = container.querySelector('.empty-state');
    expect(empty?.textContent).toContain('No charted threat data');
  });

  it('shows stable fallback on TypeError — not raw Failed to fetch', async () => {
    mockGetThreat.mockRejectedValue(new TypeError('Failed to fetch'));
    await mount();
    const alert = container.querySelector('.threat-warnline[role="alert"]');
    expect(alert?.textContent).toMatch(/check your connection/i);
    expect(alert?.textContent).not.toMatch(/Failed to fetch/i);
  });
});
