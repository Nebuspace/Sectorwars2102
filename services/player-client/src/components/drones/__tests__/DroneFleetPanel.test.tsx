// @vitest-environment jsdom
/**
 * DroneFleetPanel + droneFleetAPI — LEG-277
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const {
  mockGetTypes,
  mockGetMyDrones,
  mockCreate,
  mockRepair,
  mockUpgrade,
  mockDeploy,
} = vi.hoisted(() => ({
  mockGetTypes: vi.fn(),
  mockGetMyDrones: vi.fn(),
  mockCreate: vi.fn(),
  mockRepair: vi.fn(),
  mockUpgrade: vi.fn(),
  mockDeploy: vi.fn(),
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    currentSector: { id: '11111111-1111-1111-1111-111111111111', sector_id: 42, name: 'Test' },
    playerState: { id: 'p1', current_sector_id: 42 },
  }),
}));

vi.mock('../../../services/api', async () => {
  const actual = await vi.importActual<typeof import('../../../services/api')>('../../../services/api');
  return {
    ...actual,
    droneFleetAPI: {
      getTypes: (...a: unknown[]) => mockGetTypes(...a),
      getMyDrones: (...a: unknown[]) => mockGetMyDrones(...a),
      create: (...a: unknown[]) => mockCreate(...a),
      repair: (...a: unknown[]) => mockRepair(...a),
      upgrade: (...a: unknown[]) => mockUpgrade(...a),
      deploy: (...a: unknown[]) => mockDeploy(...a),
    },
    combatAPI: {
      ...actual.combatAPI,
      deployDrones: (...a: unknown[]) => mockDeploy(...a),
    },
  };
});

import DroneFleetPanel from '../DroneFleetPanel';

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('DroneFleetPanel', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetTypes.mockReset();
    mockGetMyDrones.mockReset();
    mockCreate.mockReset();
    mockRepair.mockReset();
    mockUpgrade.mockReset();
    mockDeploy.mockReset();
    mockGetTypes.mockResolvedValue({
      types: [
        {
          type: 'attack',
          description: 'High damage',
          base_stats: { health: 80, attack_power: 20, defense_power: 5, speed: 1.5 },
          abilities: ['precision_strike'],
        },
        {
          type: 'scout',
          description: 'Fast sensors',
          base_stats: { health: 60, attack_power: 5, defense_power: 8, speed: 2.0 },
          abilities: ['stealth'],
        },
      ],
    });
    mockGetMyDrones.mockResolvedValue([
      {
        id: 'drone-1',
        drone_type: 'attack',
        name: 'Alpha',
        level: 1,
        health: 40,
        max_health: 80,
        status: 'idle',
      },
    ]);
    mockCreate.mockResolvedValue({ id: 'drone-2', drone_type: 'scout' });
    mockRepair.mockResolvedValue({ id: 'drone-1', health: 65 });
    mockUpgrade.mockResolvedValue({ id: 'drone-1', level: 2 });
    mockDeploy.mockResolvedValue({ dronesDeployed: 1 });
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

  it('renders the type catalog', async () => {
    await act(async () => {
      root.render(<DroneFleetPanel />);
    });
    await act(async () => {
      await flush();
      await flush();
    });

    const catalog = container.querySelector('[data-testid="drone-type-catalog"]');
    expect(catalog).toBeTruthy();
    expect(catalog!.textContent).toContain('attack');
    expect(catalog!.textContent).toContain('scout');
    expect(catalog!.textContent).toContain('High damage');
  });

  it('create / repair / upgrade call through with correct args', async () => {
    await act(async () => {
      root.render(<DroneFleetPanel />);
    });
    await act(async () => {
      await flush();
      await flush();
    });

    const typeSelect = container.querySelector(
      'select[aria-label="Drone type to create"]',
    ) as HTMLSelectElement;
    await act(async () => {
      typeSelect.value = 'scout';
      typeSelect.dispatchEvent(new Event('change', { bubbles: true }));
    });

    const createBtn = Array.from(container.querySelectorAll('button')).find((b) =>
      (b.textContent || '').includes('Create'),
    ) as HTMLButtonElement;
    await act(async () => {
      createBtn.click();
      await flush();
      await flush();
    });
    expect(mockCreate).toHaveBeenCalledWith({ drone_type: 'scout' });

    const repairBtn = Array.from(container.querySelectorAll('button')).find((b) =>
      (b.textContent || '').trim() === 'Repair',
    ) as HTMLButtonElement;
    await act(async () => {
      repairBtn.click();
      await flush();
      await flush();
    });
    expect(mockRepair).toHaveBeenCalledWith('drone-1', 10);

    const upgradeBtn = Array.from(container.querySelectorAll('button')).find((b) =>
      (b.textContent || '').trim() === 'Upgrade',
    ) as HTMLButtonElement;
    await act(async () => {
      upgradeBtn.click();
      await flush();
      await flush();
    });
    expect(mockUpgrade).toHaveBeenCalledWith('drone-1');
  });
});
