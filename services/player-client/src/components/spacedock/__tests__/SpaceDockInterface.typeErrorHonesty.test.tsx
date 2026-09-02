// @vitest-environment jsdom
/**
 * LEG-3325 Soft-ORDER — SpaceDockInterface shell TypeError densify.
 * LEG-4013 Soft-ORDER — 403/429 densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SpaceDockInterface, {
  formatSpaceDockShellError,
  formatSpaceDockMarketIntelError,
  formatSpaceDockRegistryLookupError,
} from '../SpaceDockInterface';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

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

describe('formatSpaceDockShellError TypeError densify (LEG-3325)', () => {
  const fallback = 'Mining laser install failed';

  it('falls back on TypeError network collapse', () => {
    const text = formatSpaceDockShellError(new TypeError('Failed to fetch'), fallback);
    expect(text).toBe(fallback);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch non-TypeError', () => {
    expect(formatSpaceDockShellError(new Error('Network Error'), fallback)).toBe(fallback);
    expect(formatSpaceDockShellError(new Error('Failed to fetch'), fallback)).toBe(fallback);
  });

  it('preserves server detail for non-TypeError errors', () => {
    expect(formatSpaceDockShellError(new Error('Insufficient credits.'), fallback)).toBe(
      'Insufficient credits.',
    );
  });
});


describe('SpaceDockInterface 403/429 densify (LEG-4013)', () => {
  it('formatSpaceDockMarketIntelError maps 403/429 without raw transport strings', () => {
    expect(formatSpaceDockMarketIntelError(apiRequestError(403))).toBe(
      'Access denied — you cannot view market intelligence right now.',
    );
    expect(formatSpaceDockMarketIntelError(apiRequestError(403, 'market_intel_denied'))).toBe(
      'market_intel_denied',
    );
    expect(formatSpaceDockMarketIntelError(apiRequestError(429))).toBe(
      'Market intelligence rate limit exceeded — wait a moment and try again.',
    );
    expect(formatSpaceDockMarketIntelError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatSpaceDockMarketIntelError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatSpaceDockMarketIntelError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });

  it('formatSpaceDockRegistryLookupError maps 403/429 without raw transport strings', () => {
    expect(formatSpaceDockRegistryLookupError(apiRequestError(403))).toBe(
      'Access denied — registry lookup is not available right now.',
    );
    expect(formatSpaceDockRegistryLookupError(apiRequestError(429))).toBe(
      'Registry lookup rate limit exceeded — wait a moment and try again.',
    );
    expect(formatSpaceDockRegistryLookupError(apiRequestError(429))).not.toMatch(/\b429\b/);
  });

  it('formatSpaceDockShellError maps 403/429 without raw transport strings', () => {
    const fallback = 'Mining laser install failed';
    expect(formatSpaceDockShellError(apiRequestError(403), fallback)).toBe(
      'Access denied — this space-dock action is not available right now.',
    );
    expect(formatSpaceDockShellError(apiRequestError(429), fallback)).toBe(
      'Space-dock rate limit exceeded — wait a moment and try again.',
    );
    expect(formatSpaceDockShellError(apiRequestError(429), fallback)).not.toMatch(/\b429\b/);
    expect(formatSpaceDockShellError(apiRequestError(403), fallback)).not.toMatch(/TypeError/i);
  });
});

describe('SpaceDockInterface installMiningLaser TypeError densify (LEG-3325)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('accessToken', 'tok-test');
    updatePlayerCredits.mockReset();
    refreshPlayerState.mockClear();
    installEquipmentMock.mockReset();
    installEquipmentMock.mockRejectedValue(new TypeError('Failed to fetch'));
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);

    fetchMock = vi.fn(async (url: string) => {
      if (String(url).includes('/api/v1/player/current-ship')) {
        return { ok: true, json: async () => SHIP_NO_LASER };
      }
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.unstubAllGlobals();
  });

  it('installMiningLaser TypeError surfaces fallback without Failed to fetch / TypeError in DOM', async () => {
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
    await act(async () => {
      card.click();
      await flush();
    });
    await vi.waitFor(() => {
      expect(container.textContent).toMatch(/Install Mining Laser/);
    });

    const btn = Array.from(container.querySelectorAll('button.service-btn')).find((b) =>
      b.textContent?.includes('Install Mining Laser'),
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
    });

    await vi.waitFor(() => {
      expect(installEquipmentMock).toHaveBeenCalledWith('ship-1', 'mining_laser');
    });

    const alert = container.querySelector('.genesis-error-message');
    expect(alert?.textContent).toMatch(/Mining laser install failed/i);
    expect(alert?.textContent).not.toMatch(/Failed to fetch/i);
    expect(alert?.textContent).not.toMatch(/TypeError/i);
  });
});
