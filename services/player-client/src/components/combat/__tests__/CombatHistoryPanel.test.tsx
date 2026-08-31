// @vitest-environment jsdom
/**
 * CombatHistoryPanel (LEG-372) — empty / error / pagination against mocked
 * combatAPI.getHistory. Does not invent cross-player query params.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const getHistory = vi.fn();

vi.mock('../../../services/api', () => ({
  combatAPI: {
    getHistory: (...args: unknown[]) => getHistory(...args),
  },
}));

import CombatHistoryPanel from '../CombatHistoryPanel';

describe('CombatHistoryPanel', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    getHistory.mockReset();
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

  it('shows honest empty state when total is 0', async () => {
    getHistory.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
    await act(async () => {
      root.render(<CombatHistoryPanel />);
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(getHistory).toHaveBeenCalledWith({ limit: 20, offset: 0 });
    expect(container.querySelector('[data-testid="combat-history-empty"]')?.textContent).toMatch(
      /No combat history/i,
    );
  });

  it('shows honest error when fetch fails', async () => {
    getHistory.mockRejectedValue(new Error('API Error: 503'));
    await act(async () => {
      root.render(<CombatHistoryPanel />);
    });
    await act(async () => {
      await Promise.resolve();
    });
    const err = container.querySelector('[data-testid="combat-history-error"]');
    expect(err?.textContent).toContain('API Error: 503');
  });

  it('load TypeError surfaces fallback without Failed to fetch / TypeError (LEG-3163)', async () => {
    getHistory.mockRejectedValue(new TypeError('Failed to fetch'));
    await act(async () => {
      root.render(<CombatHistoryPanel />);
    });
    await act(async () => {
      await Promise.resolve();
    });
    const err = container.querySelector('[data-testid="combat-history-error"]');
    expect(err?.textContent).toBe('Failed to load combat history');
    expect(err?.textContent).not.toMatch(/Failed to fetch/i);
    expect(err?.textContent).not.toMatch(/TypeError/i);
  });

  it('Next bumps offset by limit (pagination + scoping assumptions)', async () => {
    getHistory
      .mockResolvedValueOnce({
        items: [
          {
            id: 'c1',
            timestamp: '2026-08-17T12:00:00Z',
            combat_type: 'ship_vs_ship',
            role: 'attacker',
            result: 'attacker_win',
            sector_id: 7,
            drones_lost: 0,
            ship_destroyed: false,
            opponent: { id: 'p2', name: 'Rival' },
          },
        ],
        total: 25,
        limit: 20,
        offset: 0,
      })
      .mockResolvedValueOnce({
        items: [
          {
            id: 'c2',
            timestamp: '2026-08-16T12:00:00Z',
            combat_type: 'ship_vs_port',
            role: 'defender',
            result: 'defender_win',
            sector_id: 3,
            drones_lost: 2,
            ship_destroyed: false,
            target: { type: 'station', id: 's1', name: 'Outpost' },
          },
        ],
        total: 25,
        limit: 20,
        offset: 20,
      });

    await act(async () => {
      root.render(<CombatHistoryPanel />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(getHistory).toHaveBeenCalledWith({ limit: 20, offset: 0 });
    expect(container.querySelector('[data-testid="combat-history-list"]')).toBeTruthy();

    const next = container.querySelector(
      '[data-testid="combat-history-next"]',
    ) as HTMLButtonElement;
    expect(next.disabled).toBe(false);

    await act(async () => {
      next.click();
      await Promise.resolve();
    });

    expect(getHistory).toHaveBeenLastCalledWith({ limit: 20, offset: 20 });
    // Client never passes a foreign player id — only limit/offset.
    for (const call of getHistory.mock.calls) {
      const arg = call[0] as Record<string, unknown>;
      expect(arg).not.toHaveProperty('player_id');
      expect(arg).not.toHaveProperty('playerId');
    }
  });

  it('renders opponent pinned medal via PlayerNamePlate (LEG-3234)', async () => {
    getHistory.mockResolvedValue({
      items: [
        {
          id: 'c1',
          timestamp: '2026-08-17T12:00:00Z',
          combat_type: 'ship_vs_ship',
          role: 'attacker',
          result: 'attacker_win',
          sector_id: 7,
          drones_lost: 0,
          ship_destroyed: false,
          opponent: {
            id: 'p2',
            displayName: 'Rival',
            pinned_medal_id: 'bronze_cluster',
            medal_count: 4,
          },
        },
      ],
      total: 1,
      limit: 20,
      offset: 0,
    });

    await act(async () => {
      root.render(<CombatHistoryPanel />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    const plate = container.querySelector('.ch-foe [data-testid="player-name-plate"]') as HTMLElement;
    expect(plate).not.toBeNull();
    expect(plate.getAttribute('data-pinned-medal')).toBe('bronze_cluster');
    expect(plate.querySelector('[data-testid="player-name-plate-count"]')?.textContent).toBe('4');
    expect(plate.textContent).toContain('Rival');
  });
});
