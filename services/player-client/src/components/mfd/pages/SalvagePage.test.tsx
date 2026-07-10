// @vitest-environment jsdom
/**
 * SalvagePage — WO-CMB-SALVAGE-LOOP-1 cockpit-ui lane.
 *
 * Mirrors GalaxyMap.chart.test.tsx's seam: jsdom + react-dom/client
 * createRoot + act(), no RTL, no new deps. The three-microtask-tick
 * flush() helper is the proven idiom for a mounted component whose
 * useEffect resolves a mocked async API call.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const mockSectorWrecks = vi.fn();
const mockSalvageWreck = vi.fn();

vi.mock('../../../services/api', () => ({
  sectorAPI: {
    sectorWrecks: (...a: unknown[]) => mockSectorWrecks(...a),
    salvageWreck: (...a: unknown[]) => mockSalvageWreck(...a),
  },
}));

const CURRENT_SECTOR = {
  id: 'sector-uuid', sector_id: 5, name: 'Test Sector', type: 'STANDARD',
  hazard_level: 0, radiation_level: 0, resources: {}, players_present: [],
};

let mockCurrentSector: typeof CURRENT_SECTOR | null = CURRENT_SECTOR;

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({ currentSector: mockCurrentSector }),
}));

import SalvagePage from './SalvagePage';

const makeWreck = (overrides: Record<string, unknown> = {}) => ({
  id: 'wreck-1',
  original_owner_id: 'owner-1',
  original_owner_name: 'Ace',
  destroyed_ship_type: 'CARGO_HAULER',
  cause: 'COMBAT',
  created_at: '2026-07-10T12:00:00+00:00',
  age_seconds: 600,
  cargo: { ore: 250 },
  would_flag_suspect: false,
  ...overrides,
});

describe('SalvagePage', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockSectorWrecks.mockReset();
    mockSalvageWreck.mockReset();
    mockCurrentSector = CURRENT_SECTOR;

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

  const mount = async () => {
    await act(async () => {
      root.render(<SalvagePage />);
    });
    await flush();
  };

  it('renders the mocked wreck list, fetched for the current numeric sector id', async () => {
    mockSectorWrecks.mockResolvedValue([makeWreck()]);

    await mount();

    expect(mockSectorWrecks).toHaveBeenCalledWith(5);
    const rows = container.querySelectorAll('.mfd-page-wreck-row');
    expect(rows.length).toBe(1);
    expect(container.querySelector('.mfd-page-wreck-owner')?.textContent).toBe('Ace');
    expect(container.querySelector('.mfd-page-wreck-ship')?.textContent).toBe('Cargo Hauler');
  });

  it('shows a clean empty state when the sector has no wrecks', async () => {
    mockSectorWrecks.mockResolvedValue([]);

    await mount();

    expect(container.querySelector('.mfd-empty')?.textContent).toBe('NO WRECKAGE IN THIS SECTOR');
    expect(container.querySelector('.mfd-page-wreck-row')).toBeNull();
  });

  it('previews the turn cost as ceil(units / 100) -- 250 units -> 3 turns', async () => {
    mockSectorWrecks.mockResolvedValue([makeWreck({ cargo: { ore: 250 } })]);
    await mount();

    await click(container.querySelector('.mfd-page-wreck-row')!);

    expect(container.querySelector('.mfd-salvage-preview')?.textContent).toBe('3 turn(s)');
  });

  it('previews the turn cost as ceil(units / 100) -- 101 units -> 2 turns', async () => {
    mockSectorWrecks.mockResolvedValue([makeWreck({ cargo: { ore: 101 } })]);
    await mount();

    await click(container.querySelector('.mfd-page-wreck-row')!);

    expect(container.querySelector('.mfd-salvage-preview')?.textContent).toBe('2 turn(s)');
  });

  it('shows the suspect-risk warning when the selected wreck would_flag_suspect is true', async () => {
    mockSectorWrecks.mockResolvedValue([makeWreck({ would_flag_suspect: true })]);
    await mount();

    await click(container.querySelector('.mfd-page-wreck-row')!);

    expect(container.querySelector('.mfd-page-warnline')).not.toBeNull();
  });

  it('shows no suspect-risk warning when would_flag_suspect is false', async () => {
    mockSectorWrecks.mockResolvedValue([makeWreck({ would_flag_suspect: false })]);
    await mount();

    await click(container.querySelector('.mfd-page-wreck-row')!);

    expect(container.querySelector('.mfd-page-warnline')).toBeNull();
  });

  it('invokes salvageWreck with the wreck id and the selected quantity on confirm', async () => {
    mockSectorWrecks.mockResolvedValue([makeWreck({ id: 'wreck-9', cargo: { ore: 250 } })]);
    mockSalvageWreck.mockResolvedValue({
      salvaged: { ore: 250 }, suspect_flagged: false, wreck_cleared: true, turns_spent: 3,
    });
    await mount();
    await click(container.querySelector('.mfd-page-wreck-row')!);

    await click(container.querySelector('.mfd-salvage-btn')!);

    expect(mockSalvageWreck).toHaveBeenCalledWith('wreck-9', 250);
  });

  it('a raced-expiry (404) salvage failure refetches the list instead of crashing', async () => {
    mockSectorWrecks
      .mockResolvedValueOnce([makeWreck({ id: 'wreck-1' })])
      .mockResolvedValueOnce([]); // gone by the time of the refetch
    mockSalvageWreck.mockRejectedValue(new Error('Wreck not found'));
    await mount();
    await click(container.querySelector('.mfd-page-wreck-row')!);

    await click(container.querySelector('.mfd-salvage-btn')!);
    await flush();

    expect(mockSectorWrecks).toHaveBeenCalledTimes(2);
    expect(container.querySelector('.mfd-mine-msg.err')?.textContent).toBe('Wreck not found');
    // The page did not crash -- it re-rendered the (now empty) refreshed list.
    expect(container.querySelector('.mfd-empty')?.textContent).toBe('NO WRECKAGE IN THIS SECTOR');
  });
});
