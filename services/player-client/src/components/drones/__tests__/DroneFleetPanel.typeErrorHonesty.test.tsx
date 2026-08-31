// @vitest-environment jsdom
/**
 * LEG-3150 Soft-ORDER — DroneFleetPanel TypeError densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DroneFleetPanel, { formatDroneFleetError } from '../DroneFleetPanel';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const {
  mockGetTypes,
  mockGetMyDrones,
  mockCreate,
  mockGetDeployedDrones,
} = vi.hoisted(() => ({
  mockGetTypes: vi.fn(),
  mockGetMyDrones: vi.fn(),
  mockCreate: vi.fn(),
  mockGetDeployedDrones: vi.fn(),
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
      repair: vi.fn(),
      upgrade: vi.fn(),
      deploy: vi.fn(),
      deployOne: vi.fn(),
      recall: vi.fn(),
    },
    combatAPI: {
      ...actual.combatAPI,
      deployDrones: vi.fn(),
      getDeployedDrones: (...a: unknown[]) => mockGetDeployedDrones(...a),
      recallDrones: vi.fn(),
    },
  };
});

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('formatDroneFleetError TypeError densify (LEG-3150)', () => {
  const fallback = 'Could not load drone fleet.';

  it('returns fallback on TypeError network collapse', () => {
    const text = formatDroneFleetError(new TypeError('Failed to fetch'), fallback);
    expect(text).toBe(fallback);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves gameserver detail on Error objects', () => {
    expect(formatDroneFleetError(new Error('yard_capacity_exceeded'), fallback)).toBe(
      'yard_capacity_exceeded',
    );
  });
});

describe('DroneFleetPanel load + create TypeError densify (LEG-3150)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetTypes.mockReset();
    mockGetMyDrones.mockReset();
    mockCreate.mockReset();
    mockGetDeployedDrones.mockReset();
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

  it('load TypeError surfaces fallback without Failed to fetch', async () => {
    mockGetTypes.mockRejectedValue(new TypeError('Failed to fetch'));
    mockGetMyDrones.mockRejectedValue(new TypeError('Failed to fetch'));
    mockGetDeployedDrones.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(<DroneFleetPanel />);
    });
    await act(async () => {
      await flush();
      await flush();
    });

    const alert = container.querySelector('.drone-fleet-error');
    expect(alert?.textContent).toMatch(/Could not load drone fleet/i);
    expect(alert?.textContent).not.toMatch(/Failed to fetch/i);
    expect(alert?.textContent).not.toMatch(/TypeError/i);
  });

  it('create TypeError surfaces fallback without Failed to fetch', async () => {
    mockGetTypes.mockResolvedValue({ types: [{ type: 'attack', description: 'Atk', base_stats: {} }] });
    mockGetMyDrones.mockResolvedValue([]);
    mockGetDeployedDrones.mockResolvedValue({ deployments: [] });
    mockCreate.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(<DroneFleetPanel />);
    });
    await act(async () => {
      await flush();
      await flush();
    });

    const createBtn = container.querySelector('button.primary') as HTMLButtonElement;
    await act(async () => {
      createBtn.click();
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      expect(mockCreate).toHaveBeenCalled();
    });

    const alert = container.querySelector('.drone-fleet-error');
    expect(alert?.textContent).toMatch(/Drone fleet action failed/i);
    expect(alert?.textContent).not.toMatch(/Failed to fetch/i);
    expect(alert?.textContent).not.toMatch(/TypeError/i);
  });
});
