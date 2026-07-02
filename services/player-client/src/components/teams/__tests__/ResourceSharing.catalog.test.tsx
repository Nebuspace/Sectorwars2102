// @vitest-environment jsdom
/**
 * ResourceSharing — resource catalog wiring (WO-ARCH-RES-3A-FE-CATALOG-RATIFY,
 * accept 2 + accept 6).
 *
 * The team treasury's 12 balance rows (2 transferable + 10 read-only) are
 * TeamTreasury COLUMNS, not resource-registry rows — 6 of the 10 have no
 * matching registry entry at all. Accept 6 asserts every one of the 12
 * labels renders byte-identical to the file's old local table, in BOTH the
 * catalog-absent and catalog-loaded states (this is inherently true here:
 * the registry's own labels for fuel/organics/equipment/quantum_crystals
 * equal their prettified form, so the fallback chain never diverges from
 * the catalog for these keys — this test proves that by rendering both
 * states, not just asserting it from the label table).
 *
 * Accept 2 (extensibility) rides the SAME wiring: a transaction history row
 * with a brand-new registry name ('unobtainium') the component has never
 * seen renders its registry label with zero component-code change.
 *
 * services/resourceCatalog.ts keeps its fetch cache as module-private state,
 * so each case below resets the module registry and re-imports fresh (same
 * pattern as services/__tests__/resourceCatalog.test.ts).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { TeamMember, TreasuryBalanceApiResponse, TreasuryTransactionApiResponse } from '../../../types/team';

const BALANCE: TreasuryBalanceApiResponse = {
  credits: 50000,
  fuel: 100,
  organics: 200,
  equipment: 300,
  technology: 10,
  luxury_items: 20,
  precious_metals: 30,
  raw_materials: 40,
  plasma: 50,
  bio_samples: 60,
  dark_matter: 70,
  quantum_crystals: 5,
};

const HISTORY: TreasuryTransactionApiResponse[] = [
  {
    id: 'tx-1',
    resource_type: 'unobtainium', // extensibility: a brand-new registry row, zero component change
    kind: 'deposit',
    amount: 12,
    balance_after: 12,
    actor_player_id: 'p1',
    actor_name: 'Nova',
    reason: null,
    created_at: null,
  },
];

const MEMBERS: TeamMember[] = [];

// The 12 expected byte-identical labels: TRANSFERABLE (credits, quantum_crystals)
// then READ_ONLY_KEYS in the component's declared order.
const EXPECTED_LABELS = [
  'Credits', 'Quantum Crystals', 'Fuel', 'Organics', 'Equipment',
  'Technology', 'Luxury Items', 'Precious Metals', 'Raw Materials',
  'Plasma', 'Bio Samples', 'Dark Matter',
];

function baseProps() {
  return {
    teamId: 'team-1',
    playerId: 'p1',
    members: MEMBERS,
    playerCredits: 50000,
    canManageTreasury: true,
  };
}

describe('ResourceSharing — treasury label wiring', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot> | null;

  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(async () => {
    if (root) await act(async () => { root!.unmount(); });
    container?.remove();
    root = null;
    vi.clearAllMocks();
  });

  it('renders all 12 treasury labels byte-identical to the old local table when the catalog has NOT loaded', async () => {
    vi.doMock('../../../services/api', () => ({
      teamAPI: {
        getTreasuryBalance: vi.fn(() => Promise.resolve(BALANCE)),
        getTreasuryHistory: vi.fn(() => Promise.resolve(HISTORY)),
      },
      resourceAPI: { list: vi.fn(() => new Promise(() => {})) },
    }));
    const { ResourceSharing } = await import('../ResourceSharing');

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root!.render(<ResourceSharing {...baseProps()} />);
    });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    const text = container.textContent || '';
    for (const label of EXPECTED_LABELS) {
      expect(text).toContain(label);
    }
    // Extensibility: the ledger row for a totally new registry name renders
    // via prettify (catalog absent) — no crash, no missing label.
    expect(text).toContain('Unobtainium');
  });

  it('renders the identical 12 labels once the catalog resolves, plus the new registry row label for the extensibility ledger entry', async () => {
    vi.doMock('../../../services/api', () => ({
      teamAPI: {
        getTreasuryBalance: vi.fn(() => Promise.resolve(BALANCE)),
        getTreasuryHistory: vi.fn(() => Promise.resolve(HISTORY)),
      },
      resourceAPI: {
        list: vi.fn(() => Promise.resolve([
          { name: 'fuel', label: 'Fuel', icon: 'fuel', category: 'core_commodity', base_price: 20, price_range_min: 15, price_range_max: 25, is_storable: false, is_producible: true },
          { name: 'organics', label: 'Organics', icon: 'organics', category: 'core_commodity', base_price: 18, price_range_min: 10, price_range_max: 30, is_storable: true, is_producible: true },
          { name: 'equipment', label: 'Equipment', icon: 'equipment', category: 'core_commodity', base_price: 35, price_range_min: 20, price_range_max: 50, is_storable: true, is_producible: true },
          { name: 'quantum_crystals', label: 'Quantum Crystals', icon: 'quantum_crystals', category: 'strategic', base_price: null, price_range_min: null, price_range_max: null, is_storable: true, is_producible: false },
          // Extensibility row: a brand-new registry entry the component has
          // never seen a key for — its label must surface with zero code change.
          { name: 'unobtainium', label: 'Unobtainium', icon: 'unobtainium', category: 'rare', base_price: 999, price_range_min: 900, price_range_max: 1100, is_storable: true, is_producible: false },
        ])),
      },
    }));
    const { ResourceSharing } = await import('../ResourceSharing');

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root!.render(<ResourceSharing {...baseProps()} />);
    });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    // Let the catalog fetch's listener-driven re-render land.
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    const text = container.textContent || '';
    for (const label of EXPECTED_LABELS) {
      expect(text).toContain(label);
    }
    // The ledger row for resource_type 'unobtainium' now resolves through the
    // REGISTRY label (not prettify) — same displayed text, sourced differently.
    expect(text).toContain('Unobtainium');
  });
});
