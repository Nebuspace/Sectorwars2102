// @vitest-environment jsdom
/**
 * EmpireProductionDashboard — loading / error / empty / totals / per-colony rows.
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

import EmpireProductionDashboard, {
  formatEmpireProductionLoadError,
} from '../EmpireProductionDashboard';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const PLANET_A = {
  id: 'p1',
  name: 'New Haven',
  sectorName: 'Alpha',
  colonists: 1000,
  maxColonists: 5000,
  underSiege: false,
  productionRates: { fuel: 120, organics: 80, equipment: 40, colonists: 10, research: 0 },
  allocations: { fuel: 300, organics: 300, equipment: 200, unused: 200 },
};

const PLANET_B = {
  id: 'p2',
  name: 'Quiet Rock',
  sectorName: 'Beta',
  colonists: 500,
  maxColonists: 1000,
  underSiege: true,
  productionRates: { fuel: 30, organics: 50, equipment: 20, colonists: 5, research: 0 },
  allocations: { fuel: 200, organics: 150, equipment: 100, unused: 50 },
};

describe('EmpireProductionDashboard', () => {
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
      root.render(<EmpireProductionDashboard />);
    });
    expect(container.textContent).toContain('Loading…');

    await act(async () => {
      resolve({ planets: [] });
      await flush();
    });
    expect(container.textContent).toContain('No Production Data');
  });

  it('shows error copy when the fetch fails', async () => {
    getOwnedPlanets.mockRejectedValue(new Error('boom'));

    await act(async () => {
      root.render(<EmpireProductionDashboard />);
    });
    await act(async () => {
      await flush();
    });
    expect(container.querySelector('.sb-production-error')?.textContent).toBe(
      'Failed to load production data',
    );
  });

  it('formatEmpireProductionLoadError falls back on TypeError network collapse (LEG-3173)', () => {
    const text = formatEmpireProductionLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to load production data');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('surfaces honest load fallback when getOwnedPlanets rejects with TypeError', async () => {
    getOwnedPlanets.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(<EmpireProductionDashboard />);
    });
    await act(async () => {
      await flush();
    });

    const errorEl = container.querySelector('.sb-production-error');
    expect(errorEl?.textContent).toBe('Failed to load production data');
    expect(errorEl?.textContent).not.toMatch(/Failed to fetch/i);
    expect(errorEl?.textContent).not.toMatch(/TypeError/i);
  });

  it('renders EmptyState when there are no colonies', async () => {
    getOwnedPlanets.mockResolvedValue({ planets: [] });

    await act(async () => {
      root.render(<EmpireProductionDashboard />);
    });
    await act(async () => {
      await flush();
    });
    expect(container.textContent).toContain('No Production Data');
  });

  it('aggregates empire totals and lists per-colony production', async () => {
    getOwnedPlanets.mockResolvedValue({ planets: [PLANET_A, PLANET_B] });

    await act(async () => {
      root.render(<EmpireProductionDashboard />);
    });
    await act(async () => {
      await flush();
    });

    const dash = container.querySelector('[data-testid="empire-production-dashboard"]');
    expect(dash).toBeTruthy();

    const totals = container.querySelector('[data-testid="empire-production-totals"]');
    expect(totals?.textContent).toContain('150/day'); // fuel 120+30
    expect(totals?.textContent).toContain('130/day'); // organics 80+50
    expect(totals?.textContent).toContain('60/day'); // equipment 40+20
    expect(totals?.textContent).toContain('1.5K'); // population 1500
    expect(totals?.textContent).toContain('under siege');

    expect(container.textContent).toContain('New Haven');
    expect(container.textContent).toContain('Quiet Rock');
    expect(container.textContent).toContain('Alpha');
    expect(container.textContent).toContain('Beta');

    const rowA = container.querySelector('[data-testid="empire-production-row-p1"]');
    expect(rowA?.textContent).toContain('80%'); // (1000-200)/1000

    const rowB = container.querySelector('[data-testid="empire-production-row-p2"]');
    expect(rowB?.textContent).toContain('90%'); // (500-50)/500

    expect(container.textContent).toContain('Read-only summary');
  });

  it('handles missing productionRates defensively', async () => {
    getOwnedPlanets.mockResolvedValue({
      planets: [{ ...PLANET_A, productionRates: undefined as unknown as typeof PLANET_A.productionRates }],
    });

    await act(async () => {
      root.render(<EmpireProductionDashboard />);
    });
    await act(async () => {
      await flush();
    });

    const totals = container.querySelector('[data-testid="empire-production-totals"]');
    expect(totals?.textContent).toContain('0/day');
  });
});
