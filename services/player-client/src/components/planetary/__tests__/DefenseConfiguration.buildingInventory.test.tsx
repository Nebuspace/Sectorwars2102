// @vitest-environment jsdom
/**
 * DefenseConfiguration — citadel defense building inventory (LEG-3970).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { Planet } from '../../../types/planetary';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockGetDefensePricing, mockGetAvailableBuildings } = vi.hoisted(() => ({
  mockGetDefensePricing: vi.fn(async () => ({ turrets: 380, fighters: 5000 })),
  mockGetAvailableBuildings: vi.fn(async () => ({
    success: true,
    buildings: [
      {
        type: 'orbital_platform',
        name: 'Orbital Defense Platform',
        current_count: 2,
        queued_count: 0,
      },
      {
        type: 'rail_gun',
        name: 'Rail Gun Battery',
        current_count: 4,
        queued_count: 1,
      },
      {
        type: 'scanner_array',
        name: 'Scanner Array',
        current_count: 0,
        queued_count: 0,
      },
    ],
  })),
}));

vi.mock('../../../services/api', () => ({
  gameAPI: {
    planetary: {
      getDefensePricing: mockGetDefensePricing,
      updateDefenses: vi.fn(),
    },
  },
  citadelAPI: {
    getAvailableBuildings: mockGetAvailableBuildings,
  },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({ playerState: { credits: 1000000 }, refreshPlayerState: vi.fn() }),
}));

import { DefenseConfiguration } from '../DefenseConfiguration';

const PLANET: Planet = {
  id: 'planet-1',
  name: 'Test World',
  sectorId: '1',
  sectorName: 'Sol',
  planetType: 'TERRAN',
  colonists: 100,
  maxColonists: 1000,
  productionRates: { fuel: 10, organics: 10, equipment: 10, colonists: 1, research: 0 },
  allocations: { fuel: 30, organics: 30, equipment: 30, unused: 10 },
  buildings: [],
  defenses: { turrets: 0, shields: 0, drones: 0 },
  underSiege: false,
};

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe('DefenseConfiguration building inventory (LEG-3970)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockGetDefensePricing.mockClear();
    mockGetAvailableBuildings.mockClear();
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('renders orbital platform and rail gun counts from server buildings payload', async () => {
    await act(async () => {
      root.render(<DefenseConfiguration planet={PLANET} />);
    });
    await flush();

    expect(mockGetAvailableBuildings).toHaveBeenCalledWith('planet-1');
    const section = container.querySelector('[data-testid="citadel-defense-buildings"]');
    expect(section).toBeTruthy();
    expect(section?.textContent).toMatch(/Orbital Defense Platform/);
    expect(section?.textContent).toMatch(/2 operational/);
    expect(section?.textContent).toMatch(/Rail Gun Battery/);
    expect(section?.textContent).toMatch(/4 operational/);
    expect(section?.textContent).toMatch(/1 queued/);
    expect(section?.textContent).not.toMatch(/Scanner Array/);
  });
});
