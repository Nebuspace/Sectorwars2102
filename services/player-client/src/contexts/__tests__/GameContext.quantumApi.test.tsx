// @vitest-environment jsdom
/**
 * GameContext — quantumAPI wrappers (WO-WIRE-QUANTUM-API).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const {
  mockGet,
  mockPost,
  mockGetStatus,
  mockScan,
  mockJump,
  mockRefineCharge,
  mockHarvest,
} = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockGetStatus: vi.fn(),
  mockScan: vi.fn(),
  mockJump: vi.fn(),
  mockRefineCharge: vi.fn(),
  mockHarvest: vi.fn(),
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
    quantumAPI: {
      getStatus: (...a: unknown[]) => mockGetStatus(...a),
      scan: (...a: unknown[]) => mockScan(...a),
      jump: (...a: unknown[]) => mockJump(...a),
      refineCharge: (...a: unknown[]) => mockRefineCharge(...a),
      harvest: (...a: unknown[]) => mockHarvest(...a),
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

describe('GameContext quantumAPI', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(async () => {
    mockGet.mockImplementation(defaultGet);
    mockPost.mockResolvedValue({ data: {} });
    mockGetStatus.mockResolvedValue({ quantum_charges: 1, quantum_shards: 2 });
    mockScan.mockResolvedValue({ echoes: [] });
    mockJump.mockResolvedValue({ landed_sector_id: 2 });
    mockRefineCharge.mockResolvedValue({ quantum_charges: 2, quantum_shards: 1 });
    mockHarvest.mockResolvedValue({ shards: 1, crit: false });
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

  it('routes quantum helpers through quantumAPI (no raw /quantum traffic)', async () => {
    const bearing = { bearing_deg: 90, band: 'near' };
    await act(async () => {
      await captured!.refreshQuantumStatus();
      await captured!.quantumScan(bearing as any);
      await captured!.quantumJump(bearing as any);
      await captured!.refineQuantumCharge();
      await captured!.harvestNebula();
      await flush();
    });

    expect(mockGetStatus).toHaveBeenCalled();
    expect(mockScan).toHaveBeenCalledWith(bearing);
    expect(mockJump).toHaveBeenCalledWith(bearing);
    expect(mockRefineCharge).toHaveBeenCalled();
    expect(mockHarvest).toHaveBeenCalled();

    const rawQuantum = [...mockGet.mock.calls, ...mockPost.mock.calls].filter((c) =>
      String(c[0]).includes('/quantum/'),
    );
    expect(rawQuantum).toHaveLength(0);
  });
});
