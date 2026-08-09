// @vitest-environment jsdom
/**
 * GatewrightPanel — deploy-beacon money path (WO-TESTCOV-PLAYER-GATEWRIGHT-DEPLOY).
 * DEPLOY BEACON → CONFIRM DEPLOY → warpGatesAPI.deployBeacon.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const {
  mockListMine,
  mockListSector,
  mockGetQuantum,
  mockDeployBeacon,
  mockAnchorFocus,
  mockCancel,
  mockStageMaterials,
  mockAdvanceConstruction,
} = vi.hoisted(() => ({
  mockListMine: vi.fn(),
  mockListSector: vi.fn(),
  mockGetQuantum: vi.fn(),
  mockDeployBeacon: vi.fn(),
  mockAnchorFocus: vi.fn(),
  mockCancel: vi.fn(),
  mockStageMaterials: vi.fn(),
  mockAdvanceConstruction: vi.fn(),
}));

vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api')>();
  return {
    ...actual,
    warpGatesAPI: {
      listMine: (...args: unknown[]) => mockListMine(...args),
      listSector: (...args: unknown[]) => mockListSector(...args),
      deployBeacon: (...args: unknown[]) => mockDeployBeacon(...args),
      anchorFocus: (...args: unknown[]) => mockAnchorFocus(...args),
      cancel: (...args: unknown[]) => mockCancel(...args),
      stageMaterials: (...args: unknown[]) => mockStageMaterials(...args),
      advanceConstruction: (...args: unknown[]) => mockAdvanceConstruction(...args),
    },
    quantumAPI: {
      ...actual.quantumAPI,
      getStatus: (...args: unknown[]) => mockGetQuantum(...args),
    },
  };
});

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
  mockListMine.mockResolvedValue({ projects: [] });
  mockListSector.mockResolvedValue({ gates: [], beacons: [] });
  mockGetQuantum.mockResolvedValue({
    quantum_shards: 0,
    quantum_crystals: 1,
    quantum_charges: 0,
    can_jump: true,
    is_warp_jumper: true,
    sensor_level: 1,
  });
}

describe('GatewrightPanel — deploy-beacon money path', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockListMine.mockReset();
    mockListSector.mockReset();
    mockGetQuantum.mockReset();
    mockDeployBeacon.mockReset();
    mockAnchorFocus.mockReset();
    mockCancel.mockReset();
    mockStageMaterials.mockReset();
    mockAdvanceConstruction.mockReset();
    installGetHandler();
    mockDeployBeacon.mockResolvedValue({});
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
      expect(mockDeployBeacon).toHaveBeenCalledWith(expect.any(Number));
    });

    const dest = mockDeployBeacon.mock.calls[0]?.[0] as number;
    expect(dest).toBeGreaterThanOrEqual(1);
  });
});
