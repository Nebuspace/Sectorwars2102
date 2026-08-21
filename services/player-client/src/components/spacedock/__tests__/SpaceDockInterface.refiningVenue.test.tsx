// @vitest-environment jsdom
/**
 * LEG-79 — SpaceDock Refining Facility venue opens and mounts CrystalRefiningPanel
 * (calls /api/v1/refining/* via refiningAPI). Distinct from refine-charge.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockRefine, mockStatus, mockRefreshQuantum } = vi.hoisted(() => ({
  mockRefine: vi.fn(),
  mockStatus: vi.fn(),
  mockRefreshQuantum: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ addNotification: vi.fn(), isConnected: false }),
}));

vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api')>();
  return {
    ...actual,
    refiningAPI: {
      refine: (...args: unknown[]) => mockRefine(...args),
      startLumen: vi.fn().mockResolvedValue({}),
      lumenStatus: (...args: unknown[]) => mockStatus(...args),
      collectLumen: vi.fn().mockResolvedValue({}),
    },
  };
});

const STATION_SPACEDOCK: Record<string, unknown> = {
  id: 'station-1',
  name: 'Central SpaceDock',
  type: 'SPACEDOCK',
  sector_id: 100,
  is_spacedock: true,
  services: {
    ship_dealer: true,
    ship_repair: true,
    refining_facility: true,
  },
  status: 'OPERATIONAL',
};

function makeGameState(overrides: Record<string, unknown> = {}) {
  return {
    playerState: {
      id: 'player-1',
      credits: 50_000,
      current_port_id: 'station-1',
      is_docked: true,
      attack_drones: 0,
      defense_drones: 0,
    },
    stationsInSector: [STATION_SPACEDOCK],
    marketInfo: null,
    getMarketInfo: vi.fn(),
    buyResource: vi.fn(),
    sellResource: vi.fn(),
    dockAtStation: vi.fn(),
    bumpDockOccupant: vi.fn(),
    currentShip: null,
    isLoading: false,
    error: null,
    updatePlayerCredits: vi.fn(),
    updateShipGenesis: vi.fn(),
    refreshPlayerState: vi.fn().mockResolvedValue(undefined),
    loadShips: vi.fn(),
    getStationSlips: vi.fn().mockResolvedValue(null),
    quantumStatus: {
      quantum_shards: 12,
      quantum_crystals: 1,
      quantum_charges: 0,
      jump_cooldown_until: null,
      scan_cooldown_until: null,
      can_jump: false,
      is_warp_jumper: true,
      sensor_level: 1,
    },
    refreshQuantumStatus: mockRefreshQuantum,
    ...overrides,
  };
}

let gameState: ReturnType<typeof makeGameState>;
vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => gameState,
}));

import SpaceDockInterface from '../SpaceDockInterface';

describe('SpaceDockInterface — Refining Facility venue (LEG-79)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    gameState = makeGameState();
    localStorage.clear();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockStatus.mockResolvedValue({ pending: false, ready_at: null, collectible: false });
    mockRefine.mockResolvedValue({ quantum_crystals: 2, message: 'refined' });
    mockRefreshQuantum.mockClear();
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  it('opens Refining Facility venue with crystal refining panel', async () => {
    await act(async () => {
      root.render(<SpaceDockInterface />);
    });

    const card = Array.from(container.querySelectorAll('.venue-card')).find((el) =>
      el.textContent?.includes('Refining Facility'),
    ) as HTMLElement;
    expect(card).toBeTruthy();
    expect(card.classList.contains('unavailable')).toBe(false);

    await act(async () => {
      card.click();
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(container.querySelector('[data-testid="refining-venue"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="crystal-refining-panel"]')).toBeTruthy();
    expect(container.textContent).toContain('🏭 Refining Facility');
  });

  it('refine button calls refiningAPI.refine from the venue', async () => {
    await act(async () => {
      root.render(<SpaceDockInterface />);
    });
    const card = Array.from(container.querySelectorAll('.venue-card')).find((el) =>
      el.textContent?.includes('Refining Facility'),
    ) as HTMLElement;
    await act(async () => {
      card.click();
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    const btn = container.querySelector(
      '[data-testid="refine-crystal-btn"]',
    ) as HTMLButtonElement;
    expect(btn).toBeTruthy();
    expect(btn.disabled).toBe(false);

    await act(async () => {
      btn.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(mockRefine).toHaveBeenCalled();
  });

  it('surfaces refiningAPI error detail on the panel', async () => {
    mockRefine.mockRejectedValue({
      response: { data: { detail: 'Need Class-3+ station or SpaceDock' } },
    });
    await act(async () => {
      root.render(<SpaceDockInterface />);
    });
    const card = Array.from(container.querySelectorAll('.venue-card')).find((el) =>
      el.textContent?.includes('Refining Facility'),
    ) as HTMLElement;
    await act(async () => {
      card.click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    const btn = container.querySelector(
      '[data-testid="refine-crystal-btn"]',
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    const err = container.querySelector('[data-testid="crystal-refine-error"]');
    expect(err?.textContent).toContain('Need Class-3+ station or SpaceDock');
  });
});
