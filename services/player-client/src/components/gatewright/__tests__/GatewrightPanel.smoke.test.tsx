// @vitest-environment jsdom
/**
 * LEG-3169 — GatewrightPanel smoke Vitest.
 * Mount + empty-state honesty + loading shell + onClose; no invented phases.
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

function installResolvedHandlers() {
  mockListMine.mockResolvedValue({ projects: [] });
  mockListSector.mockResolvedValue({ gates: [], beacons: [] });
  mockGetQuantum.mockResolvedValue(QUANTUM_OK);
}

describe('GatewrightPanel — smoke (LEG-3169)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockListMine.mockReset();
    mockListSector.mockReset();
    mockGetQuantum.mockReset();
    installResolvedHandlers();
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

  const mount = async (props: { onClose?: () => void } = {}) => {
    await act(async () => {
      root.render(<GatewrightPanel {...props} />);
    });
    await flush();
  };

  it('renders the header title without throw', async () => {
    await mount();
    expect(container.querySelector('.gw-hud-title')?.textContent).toBe('GATEWRIGHT GUILD CONSOLE');
    expect(container.querySelector('.gatewright-panel')).not.toBeNull();
  });

  it('shows the loading shell before the first projects fetch resolves', async () => {
    let resolveList: (v: unknown) => void = () => {};
    mockListMine.mockReturnValue(new Promise((resolve) => { resolveList = resolve; }));
    mockGetQuantum.mockResolvedValue(QUANTUM_OK);
    mockListSector.mockResolvedValue({ gates: [], beacons: [] });

    await act(async () => {
      root.render(<GatewrightPanel />);
    });

    expect(container.querySelector('.gw-state')?.textContent).toBe('Consulting the Guild registry…');

    await act(async () => {
      resolveList({ projects: [] });
    });
    await flush();

    expect(container.textContent).toContain('No gate projects on the ledger. Deploy a beacon to begin.');
  });

  it('shows empty-project honesty copy when the ledger has no live projects', async () => {
    await mount();
    expect(container.textContent).toContain('No gate projects on the ledger. Deploy a beacon to begin.');
  });

  it('hides close when onClose is omitted', async () => {
    await mount();
    expect(container.querySelector('.gw-close')).toBeNull();
  });

  it('calls onClose when the close button is clicked', async () => {
    const onClose = vi.fn();
    await mount({ onClose });

    const closeBtn = container.querySelector('.gw-close') as HTMLButtonElement;
    expect(closeBtn).not.toBeNull();
    expect(closeBtn.getAttribute('aria-label')).toBe('Close Gatewright console');

    await act(async () => {
      closeBtn.click();
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
