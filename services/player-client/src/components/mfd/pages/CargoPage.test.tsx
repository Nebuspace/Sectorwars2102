// @vitest-environment jsdom
/**
 * CargoPage — WO-UI-MAX-BATCH-1 (mfd-crgo lane, Max #15 visual re-emit).
 *
 * Mirrors ReputationPage.test.tsx / CommsCrewPage.test.tsx's seam: jsdom +
 * react-dom/client createRoot + act(), no RTL, no new deps. Pins the demo
 * re-emit (cockpit-redesign-v10-RATIFIED.html L1302-1321): a 12-segment
 * HOLD TANK gauge that fills proportionally to used/capacity, a proportional
 * cargo composition bar + swatched legend, and the pre-existing GENESIS BAY
 * lamp row rendering only when the ship actually carries a genesis bay. All
 * three read live off currentShip/useResourceCatalog — no hardcoded values.
 *
 * useResourceCatalog is left in its permanent-fallback state (mirrors
 * ContractBoardVenue.test.tsx / ConstructionVenue.catalog.test.tsx): the
 * catalog fetch never resolves, so getLabel/getColor degrade to their local
 * DEFAULT_LABELS/DEFAULT_COLORS tables deterministically.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../../services/api', () => ({
  resourceAPI: { list: vi.fn(() => new Promise(() => {})) },
}));

const baseShip = (overrides: Record<string, unknown> = {}) => ({
  id: 'ship-1',
  name: 'Test Freighter',
  type: 'FREIGHTER',
  sector_id: 5,
  cargo: { used: 30, capacity: 50, contents: { ore: 20, organics: 10 } },
  cargo_capacity: 50,
  current_speed: 1,
  base_speed: 1,
  combat: {},
  maintenance: {},
  is_flagship: false,
  purchase_value: 0,
  current_value: 0,
  genesis_devices: 0,
  max_genesis_devices: 0,
  ...overrides,
});

let mockCurrentShip: ReturnType<typeof baseShip> | null = baseShip();

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({ currentShip: mockCurrentShip }),
}));

vi.mock('../../planetary/GenesisDeployment', () => ({
  GenesisDeployment: () => <div data-testid="genesis-deployment-stub" />,
}));

import CargoPage from './CargoPage';

describe('CargoPage', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockCurrentShip = baseShip();
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

  const mount = async () => {
    await act(async () => {
      root.render(<CargoPage />);
    });
  };

  it('renders the HOLD TANK as a 12-segment SVG gauge bound to real used/capacity', async () => {
    await mount();

    const svg = container.querySelector('.mfd-cargo-tank-svg');
    expect(svg).not.toBeNull();
    const segs = container.querySelectorAll('.mfd-cargo-tank-seg');
    expect(segs.length).toBe(12);

    // used=30, capacity=50 -> 30/50*12 = 7.2 -> rounds to 7 filled segments.
    const filled = container.querySelectorAll('.mfd-cargo-tank-seg.filled');
    expect(filled.length).toBe(7);
    expect(svg?.getAttribute('aria-label')).toBe('Cargo hold 30 / 50');
  });

  it('fills proportionally when the ratio changes — no hardcoded segment count', async () => {
    mockCurrentShip = baseShip({ cargo: { used: 50, capacity: 50, contents: { ore: 50 } } });
    await mount();

    const filled = container.querySelectorAll('.mfd-cargo-tank-seg.filled');
    expect(filled.length).toBe(12);
  });

  it('renders zero filled segments when used/capacity are unavailable, without crashing', async () => {
    mockCurrentShip = baseShip({ cargo: {}, cargo_capacity: undefined as unknown as number });
    await mount();

    const filled = container.querySelectorAll('.mfd-cargo-tank-seg.filled');
    expect(filled.length).toBe(0);
    expect(container.querySelector('.mfd-cargo-tank-svg')?.getAttribute('aria-label')).toBe(
      'Cargo hold — / —',
    );
  });

  it('renders a proportional composition stack bar + swatched legend for held commodities', async () => {
    await mount();

    const stackSvg = container.querySelector('.mfd-cargo-stack-svg');
    expect(stackSvg).not.toBeNull();
    // One <rect> per held commodity in the stack bar.
    expect(stackSvg?.querySelectorAll('rect').length).toBe(2);

    const rows = container.querySelectorAll('.mfd-page-cargo-row');
    expect(rows.length).toBe(2);
    const names = Array.from(rows).map((r) => r.querySelector('.mfd-page-cargo-name')?.textContent);
    expect(names.some((n) => n?.includes('Ore'))).toBe(true);
    expect(names.some((n) => n?.includes('Organics'))).toBe(true);

    // Each legend row carries a color swatch, and distinct commodities get
    // distinct colors (not a flat placeholder).
    const swatches = Array.from(container.querySelectorAll('.mfd-cargo-swatch')) as HTMLElement[];
    expect(swatches.length).toBe(2);
    expect(swatches[0].style.backgroundColor).not.toBe('');
    expect(swatches[0].style.backgroundColor).not.toBe(swatches[1].style.backgroundColor);

    // Quantities are the real per-commodity numbers, not fabricated.
    const qtys = Array.from(rows).map((r) => r.querySelector('.mfd-page-cargo-qty')?.textContent);
    expect(qtys).toContain('× 20');
    expect(qtys).toContain('× 10');
  });

  it('shows the honest empty state and no stack/legend when the bay is empty', async () => {
    mockCurrentShip = baseShip({ cargo: { used: 0, capacity: 50, contents: {} } });
    await mount();

    expect(container.querySelector('.mfd-empty')?.textContent).toBe('CARGO BAY EMPTY');
    expect(container.querySelector('.mfd-cargo-stack-svg')).toBeNull();
    expect(container.querySelector('.mfd-page-cargo-row')).toBeNull();
  });

  it('renders the GENESIS BAY lamp row only when the ship actually has a genesis bay', async () => {
    mockCurrentShip = baseShip({ max_genesis_devices: 0, genesis_devices: 0 });
    await mount();

    expect(container.querySelector('.mfd-page-genesis-row')).toBeNull();
  });

  it('renders lit vs. unlit genesis lamps matching the real loaded/max counts', async () => {
    mockCurrentShip = baseShip({ max_genesis_devices: 3, genesis_devices: 2 });
    await mount();

    const slots = container.querySelectorAll('.mfd-page-genesis-slot');
    expect(slots.length).toBe(3);
    const loaded = container.querySelectorAll('.mfd-page-genesis-slot.loaded');
    expect(loaded.length).toBe(2);
    expect(container.querySelector('.mfd-page-genesis-count')?.textContent).toBe('2 / 3');
    // Deploy affordance appears only once at least one device is loaded.
    expect(container.querySelector('.mfd-page-genesis-deploy-btn')).not.toBeNull();
  });

  it('shows NO ACTIVE VESSEL when there is no current ship', async () => {
    mockCurrentShip = null;
    await mount();

    expect(container.querySelector('.mfd-empty')?.textContent).toBe('NO ACTIVE VESSEL');
    expect(container.querySelector('.mfd-cargo-tank-svg')).toBeNull();
  });
});
