// @vitest-environment jsdom
/**
 * GatewrightPanel — ACTIVE gate owner management (LEG-55).
 * SAVE ACCESS + TOLL → warpGatesAPI.setPermissions;
 * TRANSFER → warpGatesAPI.transfer.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const {
  mockListMine,
  mockListSector,
  mockGetQuantum,
  mockSetPermissions,
  mockSetAccessLayers,
  mockTransfer,
} = vi.hoisted(() => ({
  mockListMine: vi.fn(),
  mockListSector: vi.fn(),
  mockGetQuantum: vi.fn(),
  mockSetPermissions: vi.fn(),
  mockSetAccessLayers: vi.fn(),
  mockTransfer: vi.fn(),
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
      setPermissions: (...args: unknown[]) => mockSetPermissions(...args),
      setAccessLayers: (...args: unknown[]) => mockSetAccessLayers(...args),
      transfer: (...args: unknown[]) => mockTransfer(...args),
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

const ACTIVE_PROJECT = {
  beacon_id: 'beacon-active',
  gate_id: 'gate-1',
  phase: 'ACTIVE',
  source_sector_id: 10,
  source_name: 'Sol',
  destination_sector_id: 60,
  destination_name: 'Rigel',
  invulnerable_until: null,
  harmonization_completes_at: null,
  created_at: new Date().toISOString(),
  construction_site: null,
};

function installGetHandler() {
  mockListMine.mockResolvedValue({ projects: [ACTIVE_PROJECT] });
  mockListSector.mockResolvedValue({
    gates: [{ gate_id: 'gate-1', destination_sector_id: 60, access_mode: 'PUBLIC', toll: 100 }],
    beacons: [],
  });
  mockGetQuantum.mockResolvedValue({
    quantum_shards: 0,
    quantum_crystals: 0,
    quantum_charges: 0,
    can_jump: true,
    is_warp_jumper: true,
    sensor_level: 1,
  });
}

describe('GatewrightPanel ACTIVE management', () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    installGetHandler();
    mockSetPermissions.mockResolvedValue({
      gate_id: 'gate-1',
      mode: 'WHITELIST',
      whitelist: ['aaaa'],
      allies: [],
      is_public: false,
      toll_amount: 250,
    });
    mockSetAccessLayers.mockResolvedValue({
      gate_id: 'gate-1',
      toll_bypass: [],
      npc_factions: [],
    });
    mockTransfer.mockResolvedValue({
      gate_id: 'gate-1',
      previous_owner_id: 'me',
      new_owner_id: 'buyer',
      sale_price: 5000,
      buyer_credits: 1,
      seller_credits: 1,
      access_carried_over: 'PUBLIC',
    });
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  it('saves permissions via setPermissions', async () => {
    await act(async () => {
      root.render(<GatewrightPanel />);
      await flush();
      await flush();
    });

    expect(container.querySelector('[data-testid="gw-active-manage"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="gw-active-toll-stats-absent"]')).toBeTruthy();

    const save = container.querySelector(
      '[data-testid="gw-active-save-perms"]',
    ) as HTMLButtonElement;
    await act(async () => {
      save.click();
      await flush();
      await flush();
    });

    // Seeded from sector scan (toll 100, PUBLIC)
    expect(mockSetPermissions).toHaveBeenCalledWith(
      'gate-1',
      expect.objectContaining({ mode: 'PUBLIC', toll: 100 }),
    );
  });

  it('transfers via transfer', async () => {
    await act(async () => {
      root.render(<GatewrightPanel />);
      await flush();
      await flush();
    });

    const target = container.querySelector(
      '[data-testid="gw-active-transfer-target"]',
    ) as HTMLInputElement;
    const nativeSetter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      'value',
    )?.set;
    await act(async () => {
      nativeSetter?.call(target, 'buyer-uuid');
      target.dispatchEvent(new Event('input', { bubbles: true }));
      await flush();
    });

    const xfer = container.querySelector(
      '[data-testid="gw-active-transfer"]',
    ) as HTMLButtonElement;
    await act(async () => {
      xfer.click();
      await flush();
      await flush();
    });

    expect(mockTransfer).toHaveBeenCalledWith('gate-1', 'buyer-uuid', undefined);
  });
});
