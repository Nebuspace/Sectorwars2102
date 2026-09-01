// @vitest-environment jsdom
/**
 * GridManager — CRT-2 player-facing citadel grid (place/decommission via
 * gridAPI). jsdom + react-dom/client createRoot() + act(), no RTL, matching
 * the project convention. gridAPI mocked; resourceCatalog's resourceIcon
 * used for real (pure lookup, no deps).
 *
 * Pins: loading/error/empty(pre-citadel) states, the off-grid cell guard
 * (a plot missing from the server's plots[] renders disabled/off-grid), the
 * occupied-cell select/toggle vs hazard/uncleared-cell no-op vs
 * empty-placeable-cell popup-open branches of handleCellClick, the build
 * popup's per-entry gating (research/citadel/afford) and its resulting
 * disabled+title reason, the materials_deferred success-message suffix, the
 * decommission refund message, and that popup errors render INSIDE the
 * popup while decommission errors render outside it.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockGetGrid, mockPlace, mockDecommission, mockSurvey, mockClearPlot, mockClearHazard } = vi.hoisted(() => ({
  mockGetGrid: vi.fn(),
  mockPlace: vi.fn(),
  mockDecommission: vi.fn(),
  mockSurvey: vi.fn(),
  mockClearPlot: vi.fn(),
  mockClearHazard: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  gridAPI: {
    getGrid: mockGetGrid,
    place: mockPlace,
    decommission: mockDecommission,
    survey: mockSurvey,
    clearPlot: mockClearPlot,
    clearHazard: mockClearHazard,
  },
}));

import GridManager, { formatGridLoadError, formatGridActionError } from '../GridManager';
import type { ComponentProps } from 'react';

const catalogEntry = (overrides: Record<string, unknown> = {}) => ({
  kind: 'mine',
  name: 'Mining Rig',
  domain: 'economy',
  footprint: [1, 1],
  cost: { '1': { credits: 500, ore: 10 } },
  ...overrides,
});

const gridView = (overrides: Record<string, unknown> = {}) => ({
  success: true,
  planet_id: 'planet-1',
  cols: 2,
  rows: 1,
  plots: [
    { x: 0, y: 0, cleared: true, hazard: null, building_id: null },
    { x: 1, y: 0, cleared: true, hazard: null, building_id: null },
  ],
  buildings: [],
  citadel_level: 2,
  max_citadel_level: 5,
  catalog: [catalogEntry()],
  researched: [],
  ...overrides,
});

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe('GridManager', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetGrid.mockReset();
    mockGetGrid.mockResolvedValue(gridView());
    mockPlace.mockReset();
    mockDecommission.mockReset();
    mockSurvey.mockReset();
    mockClearPlot.mockReset();
    mockClearHazard.mockReset();
    mockDecommission.mockResolvedValue({ refund_credits: 125 });
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

  const mount = async (props: Partial<ComponentProps<typeof GridManager>> = {}) => {
    await act(async () => {
      root.render(<GridManager planetId="planet-1" playerCredits={1000} {...props} />);
    });
    await flush();
  };

  it('shows the surveying spinner before the grid loads', async () => {
    let resolveGrid: (v: unknown) => void = () => {};
    mockGetGrid.mockReturnValue(new Promise((resolve) => { resolveGrid = resolve; }));
    await act(async () => {
      root.render(<GridManager planetId="planet-1" playerCredits={1000} />);
    });
    expect(container.textContent).toContain('Surveying construction grid...');
    await act(async () => {
      resolveGrid(gridView());
    });
    await flush();
    expect(container.querySelector('.grid-board')).not.toBeNull();
  });

  it('shows a load-error with a working Retry action', async () => {
    mockGetGrid.mockRejectedValue(new Error('grid service unreachable'));
    await mount();
    expect(container.querySelector('.grid-error')?.textContent).toContain('grid service unreachable');

    mockGetGrid.mockResolvedValue(gridView());
    await act(async () => {
      (container.querySelector('.grid-retry-btn') as HTMLButtonElement).click();
    });
    await flush();
    expect(container.querySelector('.grid-board')).not.toBeNull();
  });

  it('shows the pre-citadel empty state when citadel_level < 1', async () => {
    mockGetGrid.mockResolvedValue(gridView({ citadel_level: 0 }));
    await mount();
    expect(container.querySelector('.grid-empty-title')?.textContent).toBe('No Construction Grid');
    expect(container.querySelector('.grid-board')).toBeNull();
  });

  it('renders the citadel level + max badges, and the grid aria-label', async () => {
    await mount();
    expect(container.querySelector('.grid-level-badge')?.textContent).toBe('Citadel L2');
    expect(container.querySelector('.grid-cap-badge')?.textContent).toContain('Max L5');
    expect(container.querySelector('.grid-board')?.getAttribute('aria-label')).toBe('Construction grid, 2 by 1 plots');
  });

  it('marks the size-cap badge "at-cap" when citadel_level reaches max_citadel_level', async () => {
    mockGetGrid.mockResolvedValue(gridView({ citadel_level: 5, max_citadel_level: 5 }));
    await mount();
    expect(container.querySelector('.grid-cap-badge')?.className).toContain('at-cap');
  });

  it('renders an off-grid, disabled cell for a coordinate missing from plots[]', async () => {
    mockGetGrid.mockResolvedValue(gridView({
      cols: 2, rows: 1,
      plots: [{ x: 0, y: 0, cleared: true, hazard: null, building_id: null }],
    }));
    await mount();
    const cells = Array.from(container.querySelectorAll('.grid-cell'));
    expect(cells).toHaveLength(2);
    const offGrid = cells.find((c) => c.className.includes('off-grid')) as HTMLButtonElement;
    expect(offGrid).toBeTruthy();
    expect(offGrid.disabled).toBe(true);
  });

  it('clicking a hazard cell opens the terraform panel, not the build popup', async () => {
    mockGetGrid.mockResolvedValue(gridView({
      plots: [
        { x: 0, y: 0, cleared: false, hazard: { kind: 'radiation' }, building_id: null },
        { x: 1, y: 0, cleared: true, hazard: null, building_id: null },
      ],
    }));
    await mount();
    const hazardCell = container.querySelector('.grid-cell.hazard') as HTMLButtonElement;
    await act(async () => { hazardCell.click(); });
    expect(container.querySelector('.grid-popup')).toBeNull();
    expect(container.querySelector('.grid-terraform')?.textContent).toContain('hazard');
  });

  it('clicking an uncleared cell opens the terraform panel, not the build popup', async () => {
    mockGetGrid.mockResolvedValue(gridView({
      plots: [
        { x: 0, y: 0, cleared: false, hazard: null, building_id: null },
        { x: 1, y: 0, cleared: true, hazard: null, building_id: null },
      ],
    }));
    await mount();
    const uncleared = container.querySelector('.grid-cell.uncleared') as HTMLButtonElement;
    await act(async () => { uncleared.click(); });
    expect(container.querySelector('.grid-popup')).toBeNull();
    expect(container.querySelector('.grid-terraform')?.textContent).toContain('Clear land');
  });

  it('clicking a fogged cell opens the survey terraform panel', async () => {
    mockGetGrid.mockResolvedValue(gridView({
      plots: [
        { x: 0, y: 0, cleared: true, fog: true, surveyed: false, hazard: null, building_id: null },
        { x: 1, y: 0, cleared: true, hazard: null, building_id: null },
      ],
      researched: ['t.exploration.survey.1'],
    }));
    await mount();
    const fogCell = container.querySelector('.grid-cell.fog') as HTMLButtonElement;
    await act(async () => { fogCell.click(); });
    expect(container.querySelector('.grid-terraform')?.textContent).toContain('Survey');
  });

  it('clicking an empty placeable cell opens the build popup targeting that plot', async () => {
    await mount();
    const cells = Array.from(container.querySelectorAll('.grid-cell.placeable'));
    await act(async () => { (cells[0] as HTMLButtonElement).click(); });
    expect(container.querySelector('.grid-popup')?.getAttribute('aria-label')).toBe('Build on plot (0, 0)');
  });

  it('Escape closes the build popup', async () => {
    await mount();
    await act(async () => {
      (container.querySelector('.grid-cell.placeable') as HTMLButtonElement).click();
    });
    expect(container.querySelector('.grid-popup')).not.toBeNull();
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(container.querySelector('.grid-popup')).toBeNull();
  });

  it('clicking the overlay closes the popup, but clicking inside it does not', async () => {
    await mount();
    await act(async () => {
      (container.querySelector('.grid-cell.placeable') as HTMLButtonElement).click();
    });
    await act(async () => {
      (container.querySelector('.grid-popup-header') as HTMLElement).click();
    });
    expect(container.querySelector('.grid-popup')).not.toBeNull();
    await act(async () => {
      (container.querySelector('.grid-popup-overlay') as HTMLElement).click();
    });
    expect(container.querySelector('.grid-popup')).toBeNull();
  });

  describe('catalog gating in the build popup', () => {
    const openPopup = async () => {
      await mount();
      await act(async () => {
        (container.querySelector('.grid-cell.placeable') as HTMLButtonElement).click();
      });
    };

    it('disables a research-gated entry with the requires-research reason', async () => {
      mockGetGrid.mockResolvedValue(gridView({
        catalog: [catalogEntry({ tech_gate: 'node-x' })],
        researched: [],
      }));
      await openPopup();
      const item = container.querySelector('.catalog-item') as HTMLButtonElement;
      expect(item.disabled).toBe(true);
      expect(item.className).toContain('gated');
      expect(item.title).toBe('Requires research: node-x');
    });

    it('enables an entry once its research node is in `researched`', async () => {
      mockGetGrid.mockResolvedValue(gridView({
        catalog: [catalogEntry({ tech_gate: 'node-x' })],
        researched: ['node-x'],
      }));
      await openPopup();
      const item = container.querySelector('.catalog-item') as HTMLButtonElement;
      expect(item.disabled).toBe(false);
    });

    it('disables an entry below its min_citadel_level with the requires-citadel reason', async () => {
      mockGetGrid.mockResolvedValue(gridView({
        citadel_level: 1,
        catalog: [catalogEntry({ min_citadel_level: 3 })],
      }));
      await openPopup();
      const item = container.querySelector('.catalog-item') as HTMLButtonElement;
      expect(item.disabled).toBe(true);
      expect(item.title).toBe('Requires citadel L3');
    });

    it('disables an unaffordable entry with the short-credits reason', async () => {
      mockGetGrid.mockResolvedValue(gridView({ catalog: [catalogEntry({ cost: { '1': { credits: 5000 } } })] }));
      await mount({ playerCredits: 100 });
      await act(async () => {
        (container.querySelector('.grid-cell.placeable') as HTMLButtonElement).click();
      });
      const item = container.querySelector('.catalog-item') as HTMLButtonElement;
      expect(item.disabled).toBe(true);
      expect(item.title).toBe('Need 5,000 cr (you have 100)');
    });

    it('shows "No placeable buildings available" for an empty catalog', async () => {
      mockGetGrid.mockResolvedValue(gridView({ catalog: [] }));
      await openPopup();
      expect(container.querySelector('.catalog-empty')?.textContent).toBe('No placeable buildings available.');
    });
  });

  describe('placement', () => {
    it('places on click, shows the success message, closes the popup, and refetches', async () => {
      mockPlace.mockResolvedValue({ success: true });
      await mount();
      await act(async () => {
        (container.querySelector('.grid-cell.placeable') as HTMLButtonElement).click();
      });
      mockGetGrid.mockClear();
      await act(async () => {
        (container.querySelector('.catalog-item') as HTMLButtonElement).click();
      });
      await flush();

      expect(mockPlace).toHaveBeenCalledWith('planet-1', 'mine', 0, 0, 1);
      expect(container.querySelector('.grid-popup')).toBeNull();
      expect(container.querySelector('.grid-message')?.textContent).toBe('Mining Rig enqueued at (0,0).');
      expect(mockGetGrid).toHaveBeenCalled();
    });

    it('appends the materials-deferred note when the server defers material charges', async () => {
      mockPlace.mockResolvedValue({ success: true, materials_deferred: true });
      await mount();
      await act(async () => {
        (container.querySelector('.grid-cell.placeable') as HTMLButtonElement).click();
      });
      await act(async () => {
        (container.querySelector('.catalog-item') as HTMLButtonElement).click();
      });
      await flush();
      expect(container.querySelector('.grid-message')?.textContent).toBe(
        'Mining Rig enqueued at (0,0) (materials charge deferred — credits only).'
      );
    });

    it('shows a placement error INSIDE the popup and keeps it open', async () => {
      mockPlace.mockRejectedValue(new Error('insufficient materials'));
      await mount();
      await act(async () => {
        (container.querySelector('.grid-cell.placeable') as HTMLButtonElement).click();
      });
      await act(async () => {
        (container.querySelector('.catalog-item') as HTMLButtonElement).click();
      });
      await flush();

      expect(container.querySelector('.grid-popup')).not.toBeNull();
      expect(container.querySelector('.grid-popup-error')?.textContent).toContain('insufficient materials');
      expect(container.querySelector('.grid-message')).toBeNull();
    });
  });

  describe('decommission', () => {
    const withBuilding = () => gridView({
      plots: [
        { x: 0, y: 0, cleared: true, hazard: null, building_id: 'b1' },
        { x: 1, y: 0, cleared: true, hazard: null, building_id: null },
      ],
      buildings: [{ id: 'b1', kind: 'mine', name: 'Mining Rig', domain: 'economy', x: 0, y: 0, level: 1, complete_at: null }],
    });

    it('selecting an occupied cell shows the decommission panel; clicking it again deselects', async () => {
      mockGetGrid.mockResolvedValue(withBuilding());
      await mount();
      const occupied = container.querySelector('.grid-cell.occupied') as HTMLButtonElement;
      await act(async () => { occupied.click(); });
      expect(container.querySelector('.grid-decommission')?.textContent).toContain('Mining Rig — L1');
      expect(occupied.className).toContain('selected');

      await act(async () => { occupied.click(); });
      expect(container.querySelector('.grid-decommission')).toBeNull();
    });

    it('decommissions on confirm, shows the refund message, and refetches', async () => {
      mockGetGrid.mockResolvedValue(withBuilding());
      await mount();
      await act(async () => {
        (container.querySelector('.grid-cell.occupied') as HTMLButtonElement).click();
      });
      mockGetGrid.mockClear();
      await act(async () => {
        (container.querySelector('.decomm-btn') as HTMLButtonElement).click();
      });
      await flush();

      expect(mockDecommission).toHaveBeenCalledWith('planet-1', 'b1');
      expect(container.querySelector('.grid-message')?.textContent).toBe('Decommissioned — 125 credits refunded (25% of invested).');
      expect(container.querySelector('.grid-decommission')).toBeNull();
      expect(mockGetGrid).toHaveBeenCalled();
    });

    it('shows a decommission error OUTSIDE the popup (no popup is open)', async () => {
      mockGetGrid.mockResolvedValue(withBuilding());
      mockDecommission.mockRejectedValue(new Error('cannot decommission — under siege'));
      await mount();
      await act(async () => {
        (container.querySelector('.grid-cell.occupied') as HTMLButtonElement).click();
      });
      await act(async () => {
        (container.querySelector('.decomm-btn') as HTMLButtonElement).click();
      });
      await flush();

      expect(container.querySelector('.grid-message.err')?.textContent).toBe('cannot decommission — under siege');
    });

    it('Cancel dismisses the decommission panel without calling the API', async () => {
      mockGetGrid.mockResolvedValue(withBuilding());
      await mount();
      await act(async () => {
        (container.querySelector('.grid-cell.occupied') as HTMLButtonElement).click();
      });
      await act(async () => {
        (container.querySelector('.cancel-btn') as HTMLButtonElement).click();
      });
      expect(container.querySelector('.grid-decommission')).toBeNull();
      expect(mockDecommission).not.toHaveBeenCalled();
    });
  });

  describe('terraform plot actions', () => {
    it('surveys a fogged plot when research is unlocked, then refetches', async () => {
      mockSurvey.mockResolvedValue({ success: true });
      mockGetGrid.mockResolvedValue(gridView({
        plots: [
          { x: 0, y: 0, cleared: true, fog: true, surveyed: false, hazard: null, building_id: null },
          { x: 1, y: 0, cleared: true, hazard: null, building_id: null },
        ],
        researched: ['t.exploration.survey.1'],
      }));
      await mount();
      await act(async () => {
        (container.querySelector('.grid-cell.fog') as HTMLButtonElement).click();
      });
      mockGetGrid.mockClear();
      await act(async () => {
        (container.querySelector('.terraform-btn') as HTMLButtonElement).click();
      });
      await flush();

      expect(mockSurvey).toHaveBeenCalledWith('planet-1', 0, 0);
      expect(container.querySelector('.grid-message')?.textContent).toContain('surveyed');
      expect(mockGetGrid).toHaveBeenCalled();
    });

    it('disables survey when grid_survey research is missing', async () => {
      mockGetGrid.mockResolvedValue(gridView({
        plots: [
          { x: 0, y: 0, cleared: true, fog: true, surveyed: false, hazard: null, building_id: null },
          { x: 1, y: 0, cleared: true, hazard: null, building_id: null },
        ],
        researched: [],
      }));
      await mount();
      await act(async () => {
        (container.querySelector('.grid-cell.fog') as HTMLButtonElement).click();
      });
      const btn = container.querySelector('.terraform-btn') as HTMLButtonElement;
      expect(btn.disabled).toBe(true);
      expect(btn.title).toContain('Requires research');
    });

    it('clears uncleared land when plot_clear research is unlocked', async () => {
      mockClearPlot.mockResolvedValue({ success: true });
      mockGetGrid.mockResolvedValue(gridView({
        plots: [
          { x: 0, y: 0, cleared: false, hazard: null, building_id: null },
          { x: 1, y: 0, cleared: true, hazard: null, building_id: null },
        ],
        researched: ['t.terraforming.plot_clear.1'],
      }));
      await mount();
      await act(async () => {
        (container.querySelector('.grid-cell.uncleared') as HTMLButtonElement).click();
      });
      await act(async () => {
        (container.querySelector('.terraform-btn') as HTMLButtonElement).click();
      });
      await flush();

      expect(mockClearPlot).toHaveBeenCalledWith('planet-1', 0, 0);
      expect(container.querySelector('.grid-message')?.textContent).toContain('cleared');
    });

    it('clears a hazard when hazard_clear research is unlocked', async () => {
      mockClearHazard.mockResolvedValue({ success: true });
      mockGetGrid.mockResolvedValue(gridView({
        plots: [
          { x: 0, y: 0, cleared: false, hazard: { kind: 'toxin' }, building_id: null },
          { x: 1, y: 0, cleared: true, hazard: null, building_id: null },
        ],
        researched: ['t.terraforming.hazard_clear.1'],
      }));
      await mount();
      await act(async () => {
        (container.querySelector('.grid-cell.hazard') as HTMLButtonElement).click();
      });
      await act(async () => {
        (container.querySelector('.terraform-btn') as HTMLButtonElement).click();
      });
      await flush();

      expect(mockClearHazard).toHaveBeenCalledWith('planet-1', 0, 0);
      expect(container.querySelector('.grid-message')?.textContent).toContain('remediated');
    });

    it('surfaces a 403 research-gate error from the server honestly', async () => {
      mockSurvey.mockRejectedValue(new Error("Research tool 'grid_survey' is not unlocked"));
      mockGetGrid.mockResolvedValue(gridView({
        plots: [
          { x: 0, y: 0, cleared: true, fog: true, surveyed: false, hazard: null, building_id: null },
          { x: 1, y: 0, cleared: true, hazard: null, building_id: null },
        ],
        researched: ['t.exploration.survey.1'],
      }));
      await mount();
      await act(async () => {
        (container.querySelector('.grid-cell.fog') as HTMLButtonElement).click();
      });
      await act(async () => {
        (container.querySelector('.terraform-btn') as HTMLButtonElement).click();
      });
      await flush();

      expect(container.querySelector('.grid-message.err')?.textContent).toContain(
        "Research tool 'grid_survey' is not unlocked",
      );
    });
  });
});

describe('GridManager TypeError densify (LEG-3263)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetGrid.mockReset();
    mockPlace.mockReset();
    mockGetGrid.mockResolvedValue(gridView());
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
      root.render(<GridManager planetId="planet-1" playerCredits={1000} />);
    });
    await flush();
  };

  it('formatGridLoadError falls back on TypeError network collapse', () => {
    const text = formatGridLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to load planet grid');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatGridActionError falls back on TypeError network collapse', () => {
    const text = formatGridActionError(new TypeError('Failed to fetch'), 'Placement failed');
    expect(text).toBe('Placement failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatGridLoad/Action fall back on axios Network Error / Failed to fetch (LEG-3294)', () => {
    expect(formatGridLoadError(new Error('Network Error'))).toBe('Failed to load planet grid');
    expect(formatGridLoadError(new Error('Failed to fetch'))).toBe('Failed to load planet grid');
    expect(formatGridLoadError(new Error('   '))).toBe('Failed to load planet grid');
    expect(formatGridActionError(new Error('Network Error'), 'Placement failed')).toBe('Placement failed');
    expect(formatGridActionError(new Error('plot occupied'), 'Placement failed')).toBe('plot occupied');
  });

  it('load TypeError surfaces fallback without Failed to fetch / TypeError in DOM', async () => {
    mockGetGrid.mockRejectedValue(new TypeError('Failed to fetch'));
    await mount();

    const err = container.querySelector('.grid-error');
    expect(err?.textContent).toContain('Failed to load planet grid');
    expect(err?.textContent).not.toMatch(/Failed to fetch/i);
    expect(err?.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });

  it('place TypeError surfaces fallback inside popup without Failed to fetch / TypeError in DOM', async () => {
    mockPlace.mockRejectedValue(new TypeError('Failed to fetch'));
    await mount();
    await act(async () => {
      (container.querySelector('.grid-cell.placeable') as HTMLButtonElement).click();
    });
    await act(async () => {
      (container.querySelector('.catalog-item') as HTMLButtonElement).click();
    });
    await flush();

    const err = container.querySelector('.grid-popup-error');
    expect(err?.textContent).toContain('Placement failed');
    expect(err?.textContent).not.toMatch(/Failed to fetch/i);
    expect(err?.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });
});
