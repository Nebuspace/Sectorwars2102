// @vitest-environment jsdom
/**
 * SolarSalvagePage — SOLAR SYSTEM monitor's SALVAGE page (WO-UI2-DECK-
 * RECONCILE, §05: "wreck rows → SALVAGE ▸"). Ported from mfd/pages/
 * SalvagePage.tsx's logic; mirrors SalvagePage.test.tsx's own harness.
 * `wrecks` is sourced from GameDashboard's shared state via props (no
 * GET under test here) -- only sectorAPI.salvageWreck is mocked.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { SectorWreck } from '../../../services/api';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockSalvageWreck = vi.fn();
vi.mock('../../../services/api', () => ({
  sectorAPI: {
    salvageWreck: (...a: unknown[]) => mockSalvageWreck(...a),
  },
}));

import SolarSalvagePage from '../pages/SolarSalvagePage';

const WRECK: SectorWreck = {
  id: 'wreck-1',
  original_owner_id: null,
  original_owner_name: 'Crimson Corsair',
  destroyed_ship_type: 'LIGHT_FREIGHTER',
  cause: 'combat',
  created_at: '2026-01-01T00:00:00Z',
  age_seconds: 120,
  cargo: { ore: 14, equipment: 2 },
  would_flag_suspect: false,
};

describe('SolarSalvagePage', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockSalvageWreck.mockReset();
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
    await flush();
  };

  const mount = async (wrecks: SectorWreck[], onSalvaged = vi.fn()) => {
    await act(async () => {
      root.render(<SolarSalvagePage wrecks={wrecks} onSalvaged={onSalvaged} />);
    });
    await flush();
    return onSalvaged;
  };

  it('shows an empty state with no wrecks', async () => {
    await mount([]);
    expect(container.querySelector('.empty-state')?.textContent).toBe('No wreckage in this sector');
  });

  it('lists wreck rows with ship type, owner, and age', async () => {
    await mount([WRECK]);
    const row = container.querySelector('.solar-salvage-wreck-row')!;
    expect(row.textContent).toContain('Light Freighter');
    expect(row.textContent).toContain('Crimson Corsair');
    expect(row.textContent).toContain('2m');
  });

  it('selecting a wreck reveals its cargo manifest + quantity/SALVAGE controls', async () => {
    await mount([WRECK]);
    await click(container.querySelector('.solar-salvage-wreck-row')!);

    expect(container.querySelector('.solar-salvage-detail-title')?.textContent).toContain('Light Freighter');
    const cargoText = container.querySelector('.solar-salvage-cargo-list')?.textContent || '';
    expect(cargoText).toContain('Ore');
    expect(cargoText).toContain('× 14');
    expect((container.querySelector('.solar-salvage-input') as HTMLInputElement).value).toBe('16');
  });

  it('SALVAGE ▸ calls sectorAPI.salvageWreck and reports success, then triggers onSalvaged', async () => {
    mockSalvageWreck.mockResolvedValue({
      salvaged: { ore: 14, equipment: 2 }, suspect_flagged: false, wreck_cleared: true, turns_spent: 1,
    });
    const onSalvaged = await mount([WRECK]);

    await click(container.querySelector('.solar-salvage-wreck-row')!);
    await click(container.querySelector('.solar-salvage-btn')!);

    expect(mockSalvageWreck).toHaveBeenCalledWith('wreck-1', 16);
    expect(container.querySelector('.solar-salvage-msg.ok')?.textContent).toContain('Salvaged 16 unit(s)');
    expect(onSalvaged).toHaveBeenCalled();
    // Selection clears back to the list view after a successful salvage.
    expect(container.querySelector('.solar-salvage-detail')).toBeNull();
  });

  it('a failed salvage (raced expiry) reports the error and still triggers onSalvaged to refresh the list', async () => {
    mockSalvageWreck.mockRejectedValue(new Error('Wreck not found'));
    const onSalvaged = await mount([WRECK]);

    await click(container.querySelector('.solar-salvage-wreck-row')!);
    await click(container.querySelector('.solar-salvage-btn')!);

    expect(container.querySelector('.solar-salvage-msg.err')?.textContent).toBe('Wreck not found');
    expect(onSalvaged).toHaveBeenCalled();
  });

  it('flags the SUSPECT warning inline when would_flag_suspect is true', async () => {
    await mount([{ ...WRECK, would_flag_suspect: true }]);
    expect(container.querySelector('.solar-salvage-wreck-risk')).toBeTruthy();

    await click(container.querySelector('.solar-salvage-wreck-row')!);
    expect(container.querySelector('.solar-salvage-warnline')?.textContent).toContain('SUSPECT');
  });
});
