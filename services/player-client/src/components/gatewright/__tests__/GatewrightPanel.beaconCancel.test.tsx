// @vitest-environment jsdom
/**
 * GatewrightPanel — BEACON_DEPLOYED abandon affordance (WO-NEON-RES-NH14).
 *
 * Render-only addition that reuses the existing handleCancel/armedCancelId
 * plumbing already exercised by the HARMONIZING "CANCEL ANCHOR" flow — this
 * pins the new BEACON_DEPLOYED branch: arming fires no network call, confirm
 * fires exactly one cancel via warpGatesAPI.cancel, a server 400 (the
 * harmonizing-bound guard) renders inline without hiding the card or the
 * button, a successful cancel removes the project once CANCELLED comes back
 * on refetch, and the pre-existing HARMONIZING card is untouched (no
 * abandon-beacon testid leaks into that branch).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

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
      credits: 50000,
    },
    currentShip: { type: 'WARP_JUMPER', cargo: { contents: {} } },
    refreshPlayerState: vi.fn(() => Promise.resolve()),
  }),
}));

import GatewrightPanel from '../GatewrightPanel';

const DEPLOYED_PROJECT = {
  beacon_id: 'beacon-1',
  gate_id: null,
  phase: 'BEACON_DEPLOYED',
  source_sector_id: 10,
  source_name: 'Sol',
  destination_sector_id: 60,
  destination_name: 'Rigel',
  invulnerable_until: new Date(Date.now() + 3600_000).toISOString(),
  harmonization_completes_at: null,
  created_at: new Date().toISOString(),
};

const HARMONIZING_PROJECT = {
  beacon_id: 'beacon-2',
  gate_id: 'gate-2',
  phase: 'HARMONIZING',
  source_sector_id: 10,
  source_name: 'Sol',
  destination_sector_id: 70,
  destination_name: 'Vega',
  invulnerable_until: null,
  harmonization_completes_at: new Date(Date.now() + 3600_000).toISOString(),
  created_at: new Date().toISOString(),
};

// Installs GET dispatchers; `getProjects` is read at call time so a test can
// mutate the underlying list (e.g. after a mocked cancel) and the next /mine
// poll picks up the change.
function installGetHandler(getProjects: () => unknown[]) {
  mockListMine.mockImplementation(() =>
    Promise.resolve({ projects: getProjects() }),
  );
  mockListSector.mockResolvedValue({ gates: [], beacons: [] });
  mockGetQuantum.mockResolvedValue({
    quantum_shards: 0,
    quantum_crystals: 0,
    quantum_charges: 0,
    can_jump: true,
    is_warp_jumper: true,
    sensor_level: 1,
  });
}

describe('GatewrightPanel — beacon abandon affordance', () => {
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
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  const click = async (el: Element) => {
    await act(async () => {
      el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
  };

  it('renders the abandon control on a BEACON_DEPLOYED card without firing any request', async () => {
    installGetHandler(() => [DEPLOYED_PROJECT]);

    await act(async () => {
      root.render(<GatewrightPanel />);
    });
    await flush();

    expect(container.querySelector('[data-testid="abandon-beacon"]')).toBeTruthy();
    expect(mockCancel).not.toHaveBeenCalled();
  });

  it('arms with zero network calls, then confirm fires exactly one cancel POST', async () => {
    installGetHandler(() => [DEPLOYED_PROJECT]);
    mockCancel.mockResolvedValue({});

    await act(async () => {
      root.render(<GatewrightPanel />);
    });
    await flush();

    const abandonBtn = container.querySelector('[data-testid="abandon-beacon"]') as HTMLButtonElement;
    await click(abandonBtn);
    expect(mockCancel).not.toHaveBeenCalled();

    const confirmBtn = container.querySelector('[data-testid="confirm-abandon-beacon"]') as HTMLButtonElement;
    expect(confirmBtn).toBeTruthy();
    expect(container.textContent).toContain('Quantum Crystal');

    await click(confirmBtn);
    await flush();

    expect(mockCancel).toHaveBeenCalledTimes(1);
    expect(mockCancel).toHaveBeenCalledWith('beacon-1');
  });

  it('keep/back disarms with zero requests', async () => {
    installGetHandler(() => [DEPLOYED_PROJECT]);

    await act(async () => {
      root.render(<GatewrightPanel />);
    });
    await flush();

    await click(container.querySelector('[data-testid="abandon-beacon"]') as HTMLButtonElement);
    expect(container.querySelector('[data-testid="confirm-abandon-beacon"]')).toBeTruthy();

    const keepBtn = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent === 'KEEP BEACON'
    ) as HTMLButtonElement;
    expect(keepBtn).toBeTruthy();
    await click(keepBtn);

    expect(container.querySelector('[data-testid="confirm-abandon-beacon"]')).toBeNull();
    expect(container.querySelector('[data-testid="abandon-beacon"]')).toBeTruthy();
    expect(mockCancel).not.toHaveBeenCalled();
  });

  it('renders the harmonizing-bound 400 detail inline and keeps the card + button', async () => {
    installGetHandler(() => [DEPLOYED_PROJECT]);
    mockCancel.mockRejectedValue({
      response: { data: { detail: 'Cancel the harmonizing gate first before abandoning this beacon.' } },
    });

    await act(async () => {
      root.render(<GatewrightPanel />);
    });
    await flush();

    await click(container.querySelector('[data-testid="abandon-beacon"]') as HTMLButtonElement);
    await click(container.querySelector('[data-testid="confirm-abandon-beacon"]') as HTMLButtonElement);
    await flush();

    expect(container.textContent).toContain('Cancel the harmonizing gate first');
    expect(container.querySelector('.gw-project-card')).toBeTruthy();
    // Failure leaves the confirm step armed (byte-identical to the HARMONIZING
    // flow) — the button that fired the request is still there, not hidden.
    expect(container.querySelector('[data-testid="confirm-abandon-beacon"]')).toBeTruthy();
  });

  it('removes the project from the ledger once a successful cancel refetches CANCELLED', async () => {
    let projects: unknown[] = [DEPLOYED_PROJECT];
    installGetHandler(() => projects);
    mockCancel.mockImplementation(() => {
      projects = [{ ...DEPLOYED_PROJECT, phase: 'CANCELLED' }];
      return Promise.resolve({});
    });

    await act(async () => {
      root.render(<GatewrightPanel />);
    });
    await flush();

    await click(container.querySelector('[data-testid="abandon-beacon"]') as HTMLButtonElement);
    await click(container.querySelector('[data-testid="confirm-abandon-beacon"]') as HTMLButtonElement);
    await flush();

    expect(container.querySelector('.gw-project-card')).toBeNull();
    expect(container.textContent).toContain('No gate projects on the ledger');
  });

  it('leaves the HARMONIZING card on its existing CANCEL ANCHOR flow with no abandon-beacon testid', async () => {
    installGetHandler(() => [HARMONIZING_PROJECT]);

    await act(async () => {
      root.render(<GatewrightPanel />);
    });
    await flush();

    expect(container.querySelector('[data-testid="abandon-beacon"]')).toBeNull();
    expect(container.querySelector('[data-testid="confirm-abandon-beacon"]')).toBeNull();
    const buttons = Array.from(container.querySelectorAll('button')).map((b) => b.textContent);
    expect(buttons).toContain('CANCEL ANCHOR');
  });
});
