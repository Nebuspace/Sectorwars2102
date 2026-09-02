// @vitest-environment jsdom
/**
 * LEG-3751 Soft-ORDER — CockpitColonyManagement load/mutation TypeError densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { getOwnedPlanets, mockUpgradePlanetBuilding } = vi.hoisted(() => ({
  getOwnedPlanets: vi.fn(),
  mockUpgradePlanetBuilding: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  gameAPI: {
    planetary: {
      getOwnedPlanets,
    },
  },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: { credits: 50_000 },
    upgradePlanetBuilding: mockUpgradePlanetBuilding,
  }),
}));

vi.mock('../CitadelPanel', () => ({ default: () => <div data-testid="panel-citadel" /> }));
vi.mock('../GridPanel', () => ({ default: () => <div data-testid="panel-grid" /> }));
vi.mock('../TerraformPanel', () => ({ default: () => <div data-testid="panel-terraform" /> }));
vi.mock('../ResearchPanel', () => ({ default: () => <div data-testid="panel-research" /> }));
vi.mock('../ProductionPanel', () => ({ default: () => <div data-testid="panel-production" /> }));
vi.mock('../../planetary/SpecializationDrawer', () => ({
  default: () => <div data-testid="specialization-drawer" />,
}));

import CockpitColonyManagement, {
  COCKPIT_COLONY_LOAD_FALLBACK,
  formatCockpitColonyLoadError,
} from '../CockpitColonyManagement';
import { formatBuildingUpgradeError } from '../../planetary/BuildingManager';

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

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe('formatCockpitColonyLoadError (LEG-3751)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatCockpitColonyLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe(COCKPIT_COLONY_LOAD_FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatCockpitColonyLoadError(new Error('Network Error'))).toBe(COCKPIT_COLONY_LOAD_FALLBACK);
    expect(formatCockpitColonyLoadError(new Error('Failed to fetch'))).toBe(COCKPIT_COLONY_LOAD_FALLBACK);
  });

  it('formatBuildingUpgradeError falls back on transport collapse for mutation path', () => {
    expect(formatBuildingUpgradeError(new TypeError('Failed to fetch'))).toBe('Failed to upgrade building');
    expect(formatBuildingUpgradeError(new Error('Network Error'))).toBe('Failed to upgrade building');
  });
});

describe('CockpitColonyManagement load transport collapse densify (LEG-3751)', () => {
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

  it('owned-planet load rejection surfaces role=alert fallback without raw transport text', async () => {
    getOwnedPlanets.mockRejectedValue(new Error('Network Error'));

    await act(async () => {
      root.render(<CockpitColonyManagement {...BASE_PROPS} />);
    });
    await flush();

    const alert = container.querySelector('[data-testid="cockpit-colony-load-error"]');
    expect(alert?.getAttribute('role')).toBe('alert');
    expect(alert?.textContent).toBe(COCKPIT_COLONY_LOAD_FALLBACK);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });

  it('TypeError Failed to fetch on load surfaces honest fallback without raw transport text', async () => {
    getOwnedPlanets.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(<CockpitColonyManagement {...BASE_PROPS} />);
    });
    await flush();

    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toBe(COCKPIT_COLONY_LOAD_FALLBACK);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
  });
});
