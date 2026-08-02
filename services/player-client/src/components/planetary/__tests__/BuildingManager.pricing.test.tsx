// @vitest-environment jsdom
/**
 * BuildingManager — server-authoritative nextUpgradeCost (WO-API-PHASE1 B4).
 *
 * The client no longer computes the upgrade credit cost itself (the deleted
 * serverUpgradeCost formula); it reads building.nextUpgradeCost straight off
 * the GET /planets/{id} payload (server-computed via the EXACT fn
 * upgrade_building charges). This proves: (1) the happy path renders and
 * gates on the server-supplied number, and (2) a missing/absent
 * nextUpgradeCost (stale cache, or a level-capped building) degrades
 * gracefully -- no crash, Upgrade disabled, a clear "unavailable" message --
 * rather than falling back to a guessed price.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { Planet } from '../../../types/planetary';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockUpgradeBuilding, mockPlayerState } = vi.hoisted(() => ({
  mockUpgradeBuilding: vi.fn(async () => ({ success: true, completionTime: '2026-01-01T00:00:00Z' })),
  mockPlayerState: { credits: 5000 },
}));

vi.mock('../../../services/api', () => ({
  gameAPI: {
    planetary: {
      upgradeBuilding: mockUpgradeBuilding,
    },
  },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({ playerState: mockPlayerState }),
}));

import { BuildingManager } from '../BuildingManager';

const basePlanet = (buildings: Planet['buildings']): Planet => ({
  id: 'planet-1',
  name: 'Test World',
  sectorId: '1',
  sectorName: 'Sol',
  planetType: 'TERRAN',
  colonists: 100,
  maxColonists: 1000,
  productionRates: { fuel: 10, organics: 10, equipment: 10, colonists: 1, research: 0 },
  allocations: { fuel: 30, organics: 30, equipment: 30, unused: 10 },
  buildings,
  defenses: { turrets: 0, shields: 0, drones: 0 },
  underSiege: false,
});

describe('BuildingManager — server-authoritative nextUpgradeCost', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockUpgradeBuilding.mockClear();
    mockPlayerState.credits = 5000;
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
  });

  it('renders the server-priced next-level cost and enables Upgrade when affordable', async () => {
    const planet = basePlanet([
      { type: 'factory', level: 1, upgrading: false, nextUpgradeCost: { credits: 3000, resources: { equipment: 30 } } },
    ]);
    await act(async () => {
      root.render(<BuildingManager planet={planet} />);
    });

    expect(container.textContent).toContain('3,000 credits');
    const upgradeBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Upgrade');
    expect(upgradeBtn?.disabled).toBe(false);

    await act(async () => {
      upgradeBtn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(mockUpgradeBuilding).toHaveBeenCalledWith('planet-1', 'factory', 2);
  });

  it('disables Upgrade when the server price exceeds player credits', async () => {
    mockPlayerState.credits = 100;
    const planet = basePlanet([
      { type: 'factory', level: 1, upgrading: false, nextUpgradeCost: { credits: 3000, resources: { equipment: 30 } } },
    ]);
    await act(async () => {
      root.render(<BuildingManager planet={planet} />);
    });

    const upgradeBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Upgrade');
    expect(upgradeBtn?.disabled).toBe(true);
    expect(container.textContent).toContain('Missing: 2,900 credits');
  });

  it('degrades gracefully when nextUpgradeCost is missing -- no crash, Upgrade disabled, clear message', async () => {
    const planet = basePlanet([
      { type: 'factory', level: 1, upgrading: false },
    ]);
    await act(async () => {
      root.render(<BuildingManager planet={planet} />);
    });

    expect(container.textContent).toContain('Pricing unavailable');
    const upgradeBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Upgrade');
    expect(upgradeBtn?.disabled).toBe(true);

    // A disabled button dispatched anyway must never charge — belt and
    // braces on top of the DOM-level disabled attribute.
    await act(async () => {
      upgradeBtn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(mockUpgradeBuilding).not.toHaveBeenCalled();
  });
});
