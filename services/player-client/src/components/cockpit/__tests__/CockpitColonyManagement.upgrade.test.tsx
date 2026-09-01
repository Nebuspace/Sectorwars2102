// @vitest-environment jsdom
/**
 * LEG-3648 Soft-ORDER — CockpitColonyManagement building-upgrade transport-error densify.
 *
 * Proves the cockpit → BuildingManager modal path surfaces honest upgrade
 * failure copy (formatBuildingUpgradeError) without leaking raw transport
 * strings when planetaryAPI.upgradeBuilding rejects.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Planet } from '../../../types/planetary';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { getOwnedPlanets, mockUpgradeBuilding, mockPlayerState } = vi.hoisted(() => ({
  getOwnedPlanets: vi.fn(),
  mockUpgradeBuilding: vi.fn(),
  mockPlayerState: { credits: 50_000 },
}));

vi.mock('../../../services/api', () => ({
  gameAPI: {
    planetary: {
      getOwnedPlanets,
      upgradeBuilding: mockUpgradeBuilding,
    },
  },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({ playerState: mockPlayerState }),
}));

vi.mock('../CitadelPanel', () => ({
  default: ({ onOpenBuildings }: { onOpenBuildings: () => void }) => (
    <div data-testid="panel-citadel">
      <button type="button" data-testid="open-buildings" onClick={onOpenBuildings}>
        open buildings
      </button>
    </div>
  ),
}));

vi.mock('../GridPanel', () => ({ default: () => <div data-testid="panel-grid" /> }));
vi.mock('../TerraformPanel', () => ({ default: () => <div data-testid="panel-terraform" /> }));
vi.mock('../ResearchPanel', () => ({ default: () => <div data-testid="panel-research" /> }));
vi.mock('../ProductionPanel', () => ({ default: () => <div data-testid="panel-production" /> }));
vi.mock('../../planetary/SpecializationDrawer', () => ({
  default: () => <div data-testid="specialization-drawer" />,
}));

import CockpitColonyManagement from '../CockpitColonyManagement';

const OWNED_PLANET: Planet = {
  id: 'planet-1',
  name: 'Test World',
  sectorId: '1',
  sectorName: 'Sol',
  planetType: 'TERRAN',
  colonists: 100,
  maxColonists: 1000,
  productionRates: { fuel: 10, organics: 10, equipment: 10, colonists: 1, research: 0 },
  allocations: { fuel: 30, organics: 30, equipment: 30, unused: 10 },
  buildings: [
    {
      type: 'factory',
      level: 1,
      upgrading: false,
      nextUpgradeCost: { credits: 3000, resources: { equipment: 30 } },
    },
  ],
  defenses: { turrets: 0, shields: 0, drones: 0 },
  underSiege: false,
};

const BASE_PROPS = {
  planetId: 'planet-1',
  playerCredits: 50_000,
  citadelInfo: {},
  landedPlanetDetail: {},
  productionLines: [],
  overflowResources: [],
  allocations: { fuel: 0, organics: 0, equipment: 0 },
  productionRates: null,
  allocBudget: 10,
  totalColonists: 10,
  onSetAllocations: vi.fn(),
  onStoreToSafe: vi.fn(),
  onWithdrawToCargo: vi.fn(),
  onOpsChange: vi.fn(),
};

const FALLBACK = 'Failed to upgrade building';

describe('CockpitColonyManagement building upgrade transport densify (LEG-3648)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  beforeEach(() => {
    getOwnedPlanets.mockReset();
    mockUpgradeBuilding.mockReset();
    mockPlayerState.credits = 50_000;
    getOwnedPlanets.mockResolvedValue({ planets: [OWNED_PLANET] });
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

  const openBuildingsAndUpgrade = async () => {
    await act(async () => {
      root.render(<CockpitColonyManagement {...BASE_PROPS} />);
    });
    await flush();

    const openBtn = document.body.querySelector('[data-testid="open-buildings"]') as HTMLButtonElement;
    expect(openBtn).toBeTruthy();

    await act(async () => {
      openBtn.click();
    });
    await flush();

    const upgradeBtn = Array.from(document.body.querySelectorAll('button')).find(
      (b) => b.textContent === 'Upgrade',
    );
    expect(upgradeBtn).toBeTruthy();

    await act(async () => {
      upgradeBtn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();
  };

  it('TypeError Failed to fetch on upgrade shows honest fallback without raw transport text', async () => {
    mockUpgradeBuilding.mockRejectedValue(new TypeError('Failed to fetch'));
    await openBuildingsAndUpgrade();

    expect(document.body.textContent).toMatch(new RegExp(FALLBACK, 'i'));
    expect(document.body.textContent).not.toMatch(/Failed to fetch/i);
    expect(document.body.textContent).not.toMatch(/TypeError/i);
  });

  it('Network Error on upgrade shows honest fallback without raw transport text', async () => {
    mockUpgradeBuilding.mockRejectedValue(new Error('Network Error'));
    await openBuildingsAndUpgrade();

    expect(document.body.textContent).toMatch(new RegExp(FALLBACK, 'i'));
    expect(document.body.textContent).not.toMatch(/Network Error/i);
  });

  it('Failed to fetch (non-TypeError) on upgrade shows honest fallback without raw transport text', async () => {
    mockUpgradeBuilding.mockRejectedValue(new Error('Failed to fetch'));
    await openBuildingsAndUpgrade();

    expect(document.body.textContent).toMatch(new RegExp(FALLBACK, 'i'));
    expect(document.body.textContent).not.toMatch(/Failed to fetch/i);
  });
});
