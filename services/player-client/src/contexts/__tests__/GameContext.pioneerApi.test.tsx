// @vitest-environment jsdom
/**
 * GameContext — pioneerAPI wrappers (WO-WIRE-PIONEER-API).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const {
  mockGet,
  mockPost,
  mockGetOffice,
  mockBrokerContract,
  mockLoadBatch,
  mockListContracts,
  mockCancelContract,
} = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockGetOffice: vi.fn(),
  mockBrokerContract: vi.fn(),
  mockLoadBatch: vi.fn(),
  mockListContracts: vi.fn(),
  mockCancelContract: vi.fn(),
}));

vi.mock('../../services/apiClient', () => ({
  default: { get: mockGet, post: mockPost },
  getAccessToken: vi.fn(() => 'fake-access-token'),
  refreshAccessToken: vi.fn(),
}));

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual<typeof import('../../services/api')>('../../services/api');
  return {
    ...actual,
    pioneerAPI: {
      getOffice: (...a: unknown[]) => mockGetOffice(...a),
      brokerContract: (...a: unknown[]) => mockBrokerContract(...a),
      loadBatch: (...a: unknown[]) => mockLoadBatch(...a),
      listContracts: (...a: unknown[]) => mockListContracts(...a),
      cancelContract: (...a: unknown[]) => mockCancelContract(...a),
    },
    sectorAPI: {
      ...actual.sectorAPI,
      getPlanets: vi.fn().mockResolvedValue({ planets: [] }),
      getStations: vi.fn().mockResolvedValue({ stations: [] }),
    },
    messageAPI: {
      ...actual.messageAPI,
      getInbox: vi.fn().mockResolvedValue({ messages: [], unread_count: 0 }),
    },
  };
});

vi.mock('../AuthContext', () => ({
  useAuth: () => ({ user: { id: 'user-1' }, isAuthenticated: true }),
}));

import { GameProvider, useGame } from '../GameContext';

function defaultGet(url: string) {
  if (url === '/api/v1/first-login/status') {
    return Promise.resolve({ data: { requires_first_login: false } });
  }
  if (url === '/api/v1/player/state') {
    return Promise.resolve({
      data: {
        id: 'player-1',
        username: 'tester',
        credits: 1000,
        turns: 10,
        max_turns: 500,
        current_sector_id: 1,
        is_docked: false,
        is_landed: true,
        defense_drones: 0,
        attack_drones: 0,
        mines: 0,
        personal_reputation: 0,
        reputation_tier: 'unknown',
        name_color: '#fff',
        military_rank: 'Recruit',
      },
    });
  }
  if (url === '/api/v1/player/ships') {
    return Promise.resolve({ data: [] });
  }
  if (url === '/api/v1/player/current-sector') {
    return Promise.resolve({ data: { sector_id: 1, name: 'Home' } });
  }
  if (url === '/api/v1/quantum/status') {
    return Promise.resolve({
      data: {
        quantum_charges: 0,
        quantum_shards: 0,
        charge_capacity: 0,
        refine_cooldown_ends_at: null,
      },
    });
  }
  return Promise.resolve({ data: {} });
}

let captured: ReturnType<typeof useGame> | null = null;
function Consumer() {
  captured = useGame();
  return null;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('GameContext pioneerAPI', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(async () => {
    mockGet.mockImplementation(defaultGet);
    mockPost.mockResolvedValue({ data: {} });
    mockGetOffice.mockResolvedValue({ hub_id: 'hub-1' });
    mockBrokerContract.mockResolvedValue({ id: 'mc-1', cohort_total: 100 });
    mockLoadBatch.mockResolvedValue({ id: 'mc-1', loaded: 10 });
    mockListContracts.mockResolvedValue([{ id: 'mc-1' }]);
    mockCancelContract.mockResolvedValue({ id: 'mc-1', status: 'cancelled' });
    captured = null;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(
        <GameProvider>
          <Consumer />
        </GameProvider>,
      );
      await flush();
      await flush();
    });
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  it('routes pioneer office calls through pioneerAPI (no raw /pioneer traffic)', async () => {
    await act(async () => {
      await captured!.getPioneerOffice();
      await captured!.brokerMigrationContract(100);
      await captured!.loadPioneerBatch('mc-1', 10);
      await captured!.listMigrationContracts(true);
      await captured!.cancelMigrationContract('mc-1');
      await flush();
    });

    expect(mockGetOffice).toHaveBeenCalled();
    expect(mockBrokerContract).toHaveBeenCalledWith(100);
    expect(mockLoadBatch).toHaveBeenCalledWith('mc-1', 10);
    expect(mockListContracts).toHaveBeenCalledWith(true);
    expect(mockCancelContract).toHaveBeenCalledWith('mc-1');

    const rawPioneer = [...mockGet.mock.calls, ...mockPost.mock.calls].filter((c) =>
      String(c[0]).includes('/pioneer/'),
    );
    expect(rawPioneer).toHaveLength(0);
  });
});
