// @vitest-environment jsdom
/**
 * GatewrightPanel — anchor-focus money path (WO-TESTCOV-PLAYER-GATEWRIGHT-ANCHOR).
 * ANCHOR FOCUS → COMMIT → POST /api/v1/warp-gates/anchor-focus (Phase 3 credits/turns spend).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockGet, mockPost } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
}));

vi.mock('../../../services/apiClient', () => ({
  default: { get: mockGet, post: mockPost },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: {
      current_sector_id: 60,
      is_docked: false,
      is_landed: false,
      turns: 500,
      credits: 50_000,
    },
    currentShip: { type: 'WARP_JUMPER', cargo: { contents: {} } },
    refreshPlayerState: vi.fn(async () => {}),
  }),
}));

import GatewrightPanel from '../GatewrightPanel';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const ANCHOR_READY_PROJECT = {
  beacon_id: 'beacon-1',
  gate_id: null,
  phase: 'BEACON_DEPLOYED',
  source_sector_id: 10,
  source_name: 'Sol',
  destination_sector_id: 60,
  destination_name: 'Rigel',
  invulnerable_until: new Date(Date.now() - 60_000).toISOString(),
  harmonization_completes_at: null,
  created_at: new Date().toISOString(),
  construction_site: {
    site_id: 'site-1',
    phase: 3,
    status: 'READY',
    required: {},
    staged: {},
    turns_applied: 5,
  },
};

function installGetHandler() {
  mockGet.mockImplementation(async (url: string) => {
    if (url.includes('/warp-gates/mine')) {
      return { data: { projects: [ANCHOR_READY_PROJECT] } };
    }
    if (url.includes('/warp-gates/sector/')) {
      return { data: { gates: [], beacons: [] } };
    }
    if (url.includes('/quantum/status')) {
      return {
        data: {
          quantum_shards: 0,
          quantum_crystals: 0,
          quantum_charges: 0,
          can_jump: true,
          is_warp_jumper: true,
          sensor_level: 1,
        },
      };
    }
    throw new Error(`unexpected GET ${url}`);
  });
}

describe('GatewrightPanel — anchor-focus money path', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
    installGetHandler();
    mockPost.mockResolvedValue({ data: {} });
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

  it('posts anchor-focus when COMMIT — BEGIN HARMONIZATION is clicked', async () => {
    await act(async () => {
      root.render(<GatewrightPanel />);
    });
    await act(async () => {
      await flush();
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      const btn = Array.from(container.querySelectorAll('button')).find(
        (b) => b.textContent?.trim() === 'ANCHOR FOCUS',
      );
      expect(btn).toBeTruthy();
    });

    const arm = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent?.trim() === 'ANCHOR FOCUS',
    ) as HTMLButtonElement;

    await act(async () => {
      arm.click();
      await flush();
    });

    const commit = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('COMMIT — BEGIN HARMONIZATION'),
    ) as HTMLButtonElement;
    expect(commit).toBeTruthy();

    await act(async () => {
      commit.click();
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith('/api/v1/warp-gates/anchor-focus', {
        beacon_id: 'beacon-1',
        access_mode: 'PUBLIC',
      });
    });
  });
});
