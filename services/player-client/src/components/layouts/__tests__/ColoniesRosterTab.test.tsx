// @vitest-environment jsdom
/**
 * ColoniesRosterTab — loading / error / empty / roster (+ siege badge).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { getOwnedPlanets } = vi.hoisted(() => ({
  getOwnedPlanets: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  gameAPI: { planetary: { getOwnedPlanets } },
}));

import ColoniesRosterTab, { formatColoniesRosterLoadError } from '../ColoniesRosterTab';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('ColoniesRosterTab', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    getOwnedPlanets.mockReset();
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

  it('shows loading until owned planets resolve', async () => {
    let resolve!: (v: unknown) => void;
    getOwnedPlanets.mockReturnValue(new Promise((r) => { resolve = r; }));

    await act(async () => {
      root.render(<ColoniesRosterTab />);
    });
    expect(container.textContent).toContain('Loading…');

    await act(async () => {
      resolve({ planets: [] });
      await flush();
    });
    expect(container.textContent).toContain('No Colonies');
  });

  it('shows error copy when the fetch fails', async () => {
    getOwnedPlanets.mockRejectedValue(new Error('boom'));

    await act(async () => {
      root.render(<ColoniesRosterTab />);
    });
    await act(async () => {
      await flush();
    });
    expect(container.querySelector('.sb-colonies-error')?.textContent).toBe('boom');
  });

  it('formatColoniesRosterLoadError falls back on TypeError network collapse (LEG-3103)', () => {
    const text = formatColoniesRosterLoadError(
      new TypeError('Failed to fetch'),
      'Failed to load colonies',
    );
    expect(text).toMatch(/Failed to load colonies/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatColoniesRosterLoadError densifies Network Error / Failed to fetch non-TypeError (LEG-3282)', () => {
    const fallback = 'Failed to load colonies';
    expect(formatColoniesRosterLoadError(new Error('Network Error'), fallback)).toBe(fallback);
    expect(formatColoniesRosterLoadError(new Error('Failed to fetch'), fallback)).toBe(fallback);
    expect(formatColoniesRosterLoadError(new Error('   '), fallback)).toBe(fallback);
    expect(formatColoniesRosterLoadError(new Error('boom'), fallback)).toBe('boom');
  });

  it('load TypeError surfaces honest fallback without Failed to fetch / TypeError (LEG-3103)', async () => {
    getOwnedPlanets.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(<ColoniesRosterTab />);
    });
    await act(async () => {
      await flush();
    });

    const errorEl = container.querySelector('.sb-colonies-error');
    expect(errorEl?.textContent).toMatch(/Failed to load colonies/i);
    expect(errorEl?.textContent).not.toMatch(/Failed to fetch/i);
    expect(errorEl?.textContent).not.toMatch(/TypeError/i);
  });

  it('renders EmptyState when the roster is empty', async () => {
    getOwnedPlanets.mockResolvedValue({ planets: [] });

    await act(async () => {
      root.render(<ColoniesRosterTab />);
    });
    await act(async () => {
      await flush();
    });
    expect(container.textContent).toContain('No Colonies');
    expect(container.textContent).toContain('Genesis Device');
  });

  it('lists colonies with pop counts and under-siege badge', async () => {
    getOwnedPlanets.mockResolvedValue({
      planets: [
        {
          id: 'p1',
          name: 'New Haven',
          sectorName: 'Alpha',
          colonists: 1200,
          maxColonists: 5000,
          underSiege: true,
        },
        {
          id: 'p2',
          name: 'Quiet Rock',
          sectorName: 'Beta',
          colonists: 50,
          maxColonists: 100,
          underSiege: false,
        },
      ],
    });

    await act(async () => {
      root.render(<ColoniesRosterTab />);
    });
    await act(async () => {
      await flush();
    });

    expect(container.querySelector('.sb-colonies-roster')).toBeTruthy();
    expect(container.textContent).toContain('New Haven');
    expect(container.textContent).toContain('Alpha');
    expect(container.textContent).toContain('UNDER SIEGE');
    expect(container.textContent).toContain('Quiet Rock');
    expect(container.textContent).toContain('Travel there to manage.');
    const rows = container.querySelectorAll('.sb-colonies-row');
    expect(rows.length).toBe(2);
    expect(rows[1].querySelector('.sb-colonies-siege')).toBeNull();
  });

  it('shows habitability for idle planets without terraforming readout', async () => {
    getOwnedPlanets.mockResolvedValue({
      planets: [
        {
          id: 'p1',
          name: 'Quiet Rock',
          sectorName: 'Beta',
          colonists: 50,
          maxColonists: 100,
          underSiege: false,
          habitability: { score: 42 },
        },
      ],
    });

    await act(async () => {
      root.render(<ColoniesRosterTab />);
    });
    await act(async () => {
      await flush();
    });

    expect(container.querySelector('.sb-colonies-hab')?.textContent).toBe('42%');
    expect(container.querySelector('.sb-colonies-terraform')).toBeNull();
  });

  it('shows terraforming target and progress for active projects', async () => {
    getOwnedPlanets.mockResolvedValue({
      planets: [
        {
          id: 'p2',
          name: 'Dust Bowl',
          sectorName: 'Gamma',
          colonists: 500,
          maxColonists: 2000,
          underSiege: false,
          habitability: { score: 28 },
          terraforming: { active: true, target: 85, progress: 42.5 },
        },
      ],
    });

    await act(async () => {
      root.render(<ColoniesRosterTab />);
    });
    await act(async () => {
      await flush();
    });

    expect(container.querySelector('.sb-colonies-hab')?.textContent).toBe('28%');
    const tf = container.querySelector('.sb-colonies-terraform');
    expect(tf?.textContent).toContain('→ 85%');
    expect(tf?.textContent).toContain('43%');
  });
});
