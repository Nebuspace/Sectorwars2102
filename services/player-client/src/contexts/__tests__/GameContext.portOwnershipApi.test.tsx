// @vitest-environment jsdom
/**
 * GameContext — portOwnershipAPI wrappers (WO-WIRE-PORT-OWNERSHIP-API).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const {
  mockGet,
  mockPost,
  mockGetListings,
  mockGetListing,
  mockListStation,
  mockPlaceOffer,
  mockGetMyStations,
  mockSetTax,
  mockWithdraw,
  mockInject,
  mockGetTakeoverStatus,
  mockLaunchTakeover,
  mockCounterTakeover,
  mockMilitaryTakeover,
} = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockGetListings: vi.fn(),
  mockGetListing: vi.fn(),
  mockListStation: vi.fn(),
  mockPlaceOffer: vi.fn(),
  mockGetMyStations: vi.fn(),
  mockSetTax: vi.fn(),
  mockWithdraw: vi.fn(),
  mockInject: vi.fn(),
  mockGetTakeoverStatus: vi.fn(),
  mockLaunchTakeover: vi.fn(),
  mockCounterTakeover: vi.fn(),
  mockMilitaryTakeover: vi.fn(),
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
    portOwnershipAPI: {
      getListings: (...a: unknown[]) => mockGetListings(...a),
      getListing: (...a: unknown[]) => mockGetListing(...a),
      listStation: (...a: unknown[]) => mockListStation(...a),
      placeOffer: (...a: unknown[]) => mockPlaceOffer(...a),
      getMyStations: (...a: unknown[]) => mockGetMyStations(...a),
      setTax: (...a: unknown[]) => mockSetTax(...a),
      withdraw: (...a: unknown[]) => mockWithdraw(...a),
      inject: (...a: unknown[]) => mockInject(...a),
      getTakeoverStatus: (...a: unknown[]) => mockGetTakeoverStatus(...a),
      launchTakeover: (...a: unknown[]) => mockLaunchTakeover(...a),
      counterTakeover: (...a: unknown[]) => mockCounterTakeover(...a),
      militaryTakeover: (...a: unknown[]) => mockMilitaryTakeover(...a),
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
    quantumAPI: {
      ...actual.quantumAPI,
      getStatus: vi.fn().mockResolvedValue({
        quantum_charges: 0,
        quantum_shards: 0,
        charge_capacity: 0,
        refine_cooldown_ends_at: null,
      }),
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
        is_landed: false,
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
  return Promise.resolve({ data: {} });
}

let captured: ReturnType<typeof useGame> | null = null;
function Consumer() {
  captured = useGame();
  return null;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('GameContext portOwnershipAPI', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(async () => {
    mockGet.mockImplementation(defaultGet);
    mockPost.mockResolvedValue({ data: {} });
    mockGetListings.mockResolvedValue({ listings: [] });
    mockGetListing.mockResolvedValue({ listed: false });
    mockListStation.mockResolvedValue({ success: true });
    mockPlaceOffer.mockResolvedValue({ success: true });
    mockGetMyStations.mockResolvedValue({ stations: [] });
    mockSetTax.mockResolvedValue({ rate: 0.1 });
    mockWithdraw.mockResolvedValue({ success: true });
    mockGetTakeoverStatus.mockResolvedValue({ active: false });
    mockLaunchTakeover.mockResolvedValue({ success: true });
    mockCounterTakeover.mockResolvedValue({ success: true });
    mockMilitaryTakeover.mockResolvedValue({ campaign_type: 'military' });
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

  it('routes Port Office helpers through portOwnershipAPI (no raw /port-ownership traffic)', async () => {
    await act(async () => {
      await captured!.getPortListings();
      await captured!.getListing('st-1');
      await captured!.listStation('st-1');
      await captured!.placeOffer('st-1', 500);
      await captured!.getMyStations();
      await captured!.setStationTax('st-1', 0.1);
      await captured!.withdrawTreasury('st-1', 50);
      await captured!.injectTreasury('st-1', 25);
      await captured!.getTakeoverStatus('st-1');
      await captured!.launchTakeover('st-1');
      await captured!.counterTakeover('st-1', 'match');
      await captured!.militaryTakeover('st-1', 'declare');
      await flush();
    });

    expect(mockGetListings).toHaveBeenCalled();
    expect(mockGetListing).toHaveBeenCalledWith('st-1');
    expect(mockListStation).toHaveBeenCalledWith('st-1');
    expect(mockPlaceOffer).toHaveBeenCalledWith('st-1', 500);
    expect(mockGetMyStations).toHaveBeenCalled();
    expect(mockSetTax).toHaveBeenCalledWith('st-1', 0.1);
    expect(mockWithdraw).toHaveBeenCalledWith('st-1', 50);
    expect(mockInject).toHaveBeenCalledWith('st-1', 25);
    expect(mockGetTakeoverStatus).toHaveBeenCalledWith('st-1');
    expect(mockLaunchTakeover).toHaveBeenCalledWith('st-1');
    expect(mockCounterTakeover).toHaveBeenCalledWith('st-1', 'match');
    expect(mockMilitaryTakeover).toHaveBeenCalledWith('st-1', 'declare');

    const raw = [...mockGet.mock.calls, ...mockPost.mock.calls].filter((c) =>
      String(c[0]).includes('/port-ownership/'),
    );
    expect(raw).toHaveLength(0);
  });
});
