// @vitest-environment jsdom
/**
 * SpaceDockInterface — mining license + laser install/upgrade money path
 * (WO-TESTCOV-PLAYER-MINING-LICENSE + LEG-1226 / LEG-109).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ addNotification: vi.fn(), isConnected: false }),
}));

vi.mock('../../ships', () => ({
  InsuranceManager: () => null,
  MaintenanceManager: () => null,
  ModuleGridInterface: () => null,
  TIER_LABEL: {},
}));

const { installEquipmentMock } = vi.hoisted(() => ({
  installEquipmentMock: vi.fn(),
}));

vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api')>();
  return {
    ...actual,
    shipUpgradeAPI: {
      ...actual.shipUpgradeAPI,
      installEquipment: (...args: unknown[]) => installEquipmentMock(...args),
    },
    miningAPI: {
      ...actual.miningAPI,
      listLicenses: vi.fn(async () => ({
        items: [],
        total: 0,
        recently_expired_window_hours: 24,
      })),
    },
  };
});

const STATION = {
  id: 'station-1',
  name: 'Trading Post',
  type: 'TRADING',
  sector_id: 100,
  services: {
    ship_dealer: true,
    ship_repair: true,
    ship_maintenance: true,
    genesis_dealer: true,
    drone_shop: true,
    mine_dealer: true,
  },
  status: 'OPERATIONAL',
};

const PLAYER = {
  id: 'player-1',
  credits: 50_000,
  current_port_id: 'station-1',
  is_docked: true,
  attack_drones: 0,
  defense_drones: 0,
};

const updatePlayerCredits = vi.fn();
const refreshPlayerState = vi.fn().mockResolvedValue(undefined);
const gameState = {
  playerState: PLAYER,
  stationsInSector: [STATION],
  marketInfo: null,
  getMarketInfo: vi.fn(),
  buyResource: vi.fn(),
  sellResource: vi.fn(),
  dockAtStation: vi.fn(),
  bumpDockOccupant: vi.fn(),
  currentShip: { id: 'ship-1', type: 'CARGO_HAULER', name: 'Miner' },
  isLoading: false,
  error: null,
  updatePlayerCredits,
  updateShipGenesis: vi.fn(),
  refreshPlayerState,
  loadShips: vi.fn(),
  getStationSlips: vi.fn().mockResolvedValue(null),
};

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => gameState,
}));

import SpaceDockInterface from '../SpaceDockInterface';
import { miningAPI } from '../../../services/api';
import {
  __resetSpacedockVenueBusForTests,
  requestSpacedockVenue,
} from '../../../services/spacedockVenueBus';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const SHIP_NO_LASER = {
  id: 'ship-1',
  name: 'Miner',
  type: 'CARGO_HAULER',
  genesis_devices: 0,
  max_genesis_devices: 0,
  current_value: 10_000,
  cargo_capacity: 50,
  cargo: { used: 0 },
  combat: { hull: 100, max_hull: 100, shields: 50, max_shields: 50 },
  mining_laser_level: null as number | null,
};

const SHIP_WITH_LASER = {
  ...SHIP_NO_LASER,
  mining_laser_level: 1,
};

describe('SpaceDockInterface — mining license / laser', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let fetchMock: ReturnType<typeof vi.fn>;
  let shipPayload: typeof SHIP_NO_LASER;

  beforeEach(() => {
    __resetSpacedockVenueBusForTests();
    localStorage.clear();
    localStorage.setItem('accessToken', 'tok-test');
    updatePlayerCredits.mockReset();
    refreshPlayerState.mockClear();
    installEquipmentMock.mockReset();
    installEquipmentMock.mockResolvedValue({
      success: true,
      message: 'Mining Laser installed successfully',
      cost_paid: 35_000,
      remaining_credits: 15_000,
    });
    shipPayload = { ...SHIP_NO_LASER, mining_laser_level: null };
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);

    fetchMock = vi.fn(async (url: string) => {
      const u = String(url);
      if (u.includes('/api/v1/player/current-ship')) {
        return { ok: true, json: async () => shipPayload };
      }
      if (u.includes('/api/v1/mining/license')) {
        return {
          ok: true,
          json: async () => ({
            cost_paid_cr: 1500,
            expires_at: '2099-01-01T00:00:00Z',
          }),
        };
      }
      if (u.includes('/api/v1/mining/laser-upgrade')) {
        return {
          ok: true,
          json: async () => ({
            cost_paid: 2000,
            new_level: 2,
            yield_multiplier: 1.5,
            message: 'Laser upgraded',
            remaining_credits: 48_000,
          }),
        };
      }
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.mocked(miningAPI.listLicenses).mockClear();
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.unstubAllGlobals();
  });

  const openMining = async () => {
    await act(async () => {
      root.render(<SpaceDockInterface />);
      await flush();
    });
    await vi.waitFor(() => {
      expect(
        fetchMock.mock.calls.some((c) => String(c[0]).includes('/api/v1/player/current-ship')),
      ).toBe(true);
    });
    const card = Array.from(container.querySelectorAll('.venue-card')).find((el) =>
      el.textContent?.includes('Astral Mining'),
    ) as HTMLElement;
    expect(card).toBeTruthy();
    await act(async () => {
      card.click();
      await flush();
    });
    await vi.waitFor(() => {
      expect(container.textContent).toContain('Claim License');
    });
  };

  it('fetches mining/licenses when the mining venue opens', async () => {
    await openMining();
    await vi.waitFor(() => {
      expect(miningAPI.listLicenses).toHaveBeenCalled();
    });
  });

  it('posts mining/license when Purchase / Renew License is clicked', async () => {
    await openMining();
    const btn = Array.from(container.querySelectorAll('button.service-btn')).find((b) =>
      b.textContent?.includes('Purchase / Renew License'),
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
    });

    await vi.waitFor(() => {
      expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/api/v1/mining/license'))).toBe(
        true,
      );
    });
    const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/api/v1/mining/license'))!;
    const [, init] = call;
    expect(init?.method).toBe('POST');
    expect(JSON.parse(init?.body as string)).toEqual({ ship_id: 'ship-1' });
    expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer tok-test');
    await vi.waitFor(() => {
      expect(container.textContent).toMatch(/Claim filed/);
    });
  });

  it('installs Mining Laser via equipment/install when none fitted', async () => {
    await openMining();
    await vi.waitFor(() => {
      expect(container.textContent).toMatch(/Install Mining Laser/);
    });
    const btn = Array.from(container.querySelectorAll('button.service-btn')).find((b) =>
      b.textContent?.includes('Install Mining Laser'),
    ) as HTMLButtonElement;
    expect(btn).toBeTruthy();
    await act(async () => {
      btn.click();
      await flush();
    });

    await vi.waitFor(() => {
      expect(installEquipmentMock).toHaveBeenCalledWith('ship-1', 'mining_laser');
    });
    expect(
      fetchMock.mock.calls.some((c) => String(c[0]).includes('/api/v1/mining/laser-upgrade')),
    ).toBe(false);
    await vi.waitFor(() => {
      expect(container.textContent).toMatch(/Mining Laser installed/);
    });
  });

  it('posts mining/laser-upgrade when Upgrade Mining Laser is clicked', async () => {
    shipPayload = { ...SHIP_WITH_LASER };
    await openMining();
    await vi.waitFor(() => {
      expect(container.textContent).toMatch(/Upgrade Mining Laser/);
    });
    const btn = Array.from(container.querySelectorAll('button.service-btn')).find((b) =>
      b.textContent?.includes('Upgrade Mining Laser'),
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
    });

    await vi.waitFor(() => {
      expect(
        fetchMock.mock.calls.some((c) => String(c[0]).includes('/api/v1/mining/laser-upgrade')),
      ).toBe(true);
    });
    const call = fetchMock.mock.calls.find((c) =>
      String(c[0]).includes('/api/v1/mining/laser-upgrade'),
    )!;
    const [, init] = call;
    expect(init?.method).toBe('POST');
    expect(JSON.parse(init?.body as string)).toEqual({ ship_id: 'ship-1' });
    expect(installEquipmentMock).not.toHaveBeenCalled();
  });

  it('opens Astral Mining when spacedockVenueBus requests mining (license expiry deep-link)', async () => {
    await act(async () => {
      root.render(<SpaceDockInterface />);
      await flush();
    });
    await vi.waitFor(() => {
      expect(
        fetchMock.mock.calls.some((c) => String(c[0]).includes('/api/v1/player/current-ship')),
      ).toBe(true);
    });

    await act(async () => {
      requestSpacedockVenue('mining');
      await flush();
    });

    await vi.waitFor(() => {
      expect(container.textContent).toContain('Claim License');
    });
  });
});
