// @vitest-environment jsdom
/**
 * GatewrightPanel — deploy-beacon money path (WO-TESTCOV-PLAYER-GATEWRIGHT-DEPLOY).
 * DEPLOY BEACON → CONFIRM DEPLOY → POST /api/v1/warp-gates/deploy-beacon.
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
      current_sector_id: 10,
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

function installGetHandler() {
  mockGet.mockImplementation(async (url: string) => {
    if (url.includes('/warp-gates/mine')) {
      return { data: { projects: [] } };
    }
    if (url.includes('/warp-gates/sector/')) {
      return { data: { gates: [], beacons: [] } };
    }
    if (url.includes('/quantum/status')) {
      return {
        data: {
          quantum_shards: 0,
          quantum_crystals: 1,
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

describe('GatewrightPanel — deploy-beacon money path', () => {
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

  it('posts deploy-beacon when CONFIRM DEPLOY is clicked', async () => {
    await act(async () => {
      root.render(<GatewrightPanel />);
    });
    await act(async () => {
      await flush();
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      const btn = Array.from(container.querySelectorAll('button')).find((b) =>
        b.textContent?.includes('DEPLOY BEACON'),
      );
      expect(btn).toBeTruthy();
    });

    const arm = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent?.trim() === 'DEPLOY BEACON',
    ) as HTMLButtonElement;
    expect(arm.disabled).toBe(false);

    await act(async () => {
      arm.click();
      await flush();
    });

    const confirm = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent?.includes('CONFIRM DEPLOY'),
    ) as HTMLButtonElement;
    expect(confirm).toBeTruthy();

    await act(async () => {
      confirm.click();
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith('/api/v1/warp-gates/deploy-beacon', {
        destination_sector_id: expect.any(Number),
      });
    });

    const call = mockPost.mock.calls.find((c) => c[0] === '/api/v1/warp-gates/deploy-beacon');
    expect(call?.[1]).toEqual({ destination_sector_id: expect.any(Number) });
    expect((call?.[1] as { destination_sector_id: number }).destination_sector_id).toBeGreaterThanOrEqual(1);
  });
});
