// @vitest-environment jsdom
/**
 * LEG-3191 Soft-ORDER — GatewrightPanel TypeError/network honesty.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockListMine, mockListSector, mockGetQuantum } = vi.hoisted(() => ({
  mockListMine: vi.fn(),
  mockListSector: vi.fn(),
  mockGetQuantum: vi.fn(),
}));

vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api')>();
  return {
    ...actual,
    warpGatesAPI: {
      listMine: (...args: unknown[]) => mockListMine(...args),
      listSector: (...args: unknown[]) => mockListSector(...args),
      deployBeacon: vi.fn(),
      anchorFocus: vi.fn(),
      cancel: vi.fn(),
      stageMaterials: vi.fn(),
      advanceConstruction: vi.fn(),
      setPermissions: vi.fn(),
      setAccessLayers: vi.fn(),
      transfer: vi.fn(),
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

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
};

const QUANTUM_OK = {
  quantum_shards: 0,
  quantum_crystals: 1,
  quantum_charges: 0,
  can_jump: true,
  is_warp_jumper: true,
  sensor_level: 1,
};

describe('GatewrightPanel TypeError honesty (LEG-3191)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockListMine.mockReset();
    mockListSector.mockReset();
    mockGetQuantum.mockReset();
    mockGetQuantum.mockResolvedValue(QUANTUM_OK);
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

  const mount = async () => {
    await act(async () => {
      root.render(<GatewrightPanel />);
    });
    await flush();
  };

  it('listMine TypeError surfaces guild fallback without Failed to fetch / TypeError', async () => {
    mockListMine.mockRejectedValue(new TypeError('Failed to fetch'));
    mockListSector.mockResolvedValue({ gates: [], beacons: [] });

    await mount();

    const strip = container.querySelector('.gw-validation-strip');
    expect(strip?.textContent).toBe('Guild registry unreachable. Try again.');
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });

  it('listSector TypeError surfaces sector scan fallback without Failed to fetch / TypeError', async () => {
    mockListMine.mockResolvedValue({ projects: [] });
    mockListSector.mockRejectedValue(new TypeError('Failed to fetch'));

    await mount();

    const strips = container.querySelectorAll('.gw-validation-strip');
    const sectorStrip = Array.from(strips).find((el) =>
      el.textContent?.includes('Sector gate scan failed'),
    );
    expect(sectorStrip?.textContent).toBe('Sector gate scan failed. Try again.');
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });

  it('listMine axios Network Error surfaces guild fallback without raw transport (LEG-3518)', async () => {
    mockListMine.mockRejectedValue(new Error('Network Error'));
    mockListSector.mockResolvedValue({ gates: [], beacons: [] });

    await mount();

    const strip = container.querySelector('.gw-validation-strip');
    expect(strip?.textContent).toBe('Guild registry unreachable. Try again.');
    expect(container.textContent).not.toMatch(/Network Error/i);
  });

  it('listSector axios Network Error surfaces sector scan fallback without raw transport (LEG-3518)', async () => {
    mockListMine.mockResolvedValue({ projects: [] });
    mockListSector.mockRejectedValue(new Error('Network Error'));

    await mount();

    const strips = container.querySelectorAll('.gw-validation-strip');
    const sectorStrip = Array.from(strips).find((el) =>
      el.textContent?.includes('Sector gate scan failed'),
    );
    expect(sectorStrip?.textContent).toBe('Sector gate scan failed. Try again.');
    expect(container.textContent).not.toMatch(/Network Error/i);
  });
});
