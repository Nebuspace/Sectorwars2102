// @vitest-environment jsdom
/**
 * RoutePlannerPanel — first player consumer of POST /api/v1/routes/optimize
 * (WO-SB-RO2 Lane C) and GET /api/v1/routes/history (WO-ECON-ROUTE-HISTORY).
 *
 * routeOptimizerService is mocked at the module boundary (not fetch) so this
 * pins the component's own state machine: collapsed by default, expands on
 * click, a successful optimize renders the returned sector hop chain, and a
 * rejected optimize renders a visible error state with no fabricated route.
 * The "Recent Plans" strip additionally pins: collapsed by default (does
 * not displace the Plot Route form), lazy-fetches only on its own first
 * expand, an honest empty state, and that selecting a past entry renders it
 * clearly labeled as historical rather than a live result.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { mockOptimizeRoute, mockGetHistory } = vi.hoisted(() => ({
  mockOptimizeRoute: vi.fn(),
  mockGetHistory: vi.fn(),
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: { current_sector_id: 42 },
    currentShip: { cargo_capacity: 250 },
  }),
}));

vi.mock('../../../services/routeOptimizerService', () => ({
  routeOptimizerService: { optimizeRoute: mockOptimizeRoute, getHistory: mockGetHistory },
}));

import RoutePlannerPanel, {
  formatRouteOptimizeError,
  formatRouteHistoryError,
} from '../RoutePlannerPanel';

const FAKE_RESPONSE = {
  objective: 'balanced',
  route_type: 'linear',
  sectors: ['42', '17', '9'],
  total_profit: 1200,
  total_distance: 2,
  total_time_hours: 3.5,
  total_risk: 0.2,
  cargo_efficiency: 0.75,
  profit_per_hour: 342.8,
  route_confidence: 0.9,
  opportunities: [],
};

describe('RoutePlannerPanel', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockOptimizeRoute.mockReset();
    mockGetHistory.mockReset();
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
    vi.clearAllMocks();
  });

  const expandPanel = async () => {
    const header = container.querySelector('.route-planner-header') as HTMLElement;
    await act(async () => {
      header.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
  };

  it('renders collapsed by default so it never displaces the buy/sell content', async () => {
    await act(async () => {
      root.render(<RoutePlannerPanel />);
    });

    expect(container.querySelector('.route-planner-body')).toBeNull();
    expect(container.querySelector('.route-planner-header')?.textContent).toContain('Trade Route Optimizer');
    // WO-UIPC-ROUTEPLANNER-EXPLAINER: the commerce-vs-navigation explainer is
    // always visible, even collapsed, so it's not gated behind expanding the panel.
    expect(container.querySelector('.route-planner-subtitle')?.textContent).toContain('this is commerce, not navigation');
  });

  it('expands on header click and defaults the start sector to the current sector', async () => {
    await act(async () => {
      root.render(<RoutePlannerPanel />);
    });
    await expandPanel();

    const startInput = container.querySelector('#rp-start') as HTMLInputElement;
    expect(startInput.value).toBe('42');
  });

  it('renders the exact sector chain the API returned on a successful plan', async () => {
    mockOptimizeRoute.mockResolvedValue(FAKE_RESPONSE);

    await act(async () => {
      root.render(<RoutePlannerPanel />);
    });
    await expandPanel();

    const form = container.querySelector('.route-planner-form') as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });

    expect(mockOptimizeRoute).toHaveBeenCalledTimes(1);
    expect(mockOptimizeRoute.mock.calls[0][0]).toMatchObject({
      startSectorId: '42',
      objective: 'balanced',
    });

    const hops = Array.from(container.querySelectorAll('.route-planner-hop')).map(h => h.textContent);
    expect(hops).toEqual(['42', '17', '9']);
    expect(container.querySelector('.route-planner-error')).toBeNull();
  });

  it('shows a visible error state and renders no route when the API rejects', async () => {
    mockOptimizeRoute.mockRejectedValue(new Error('No viable balanced route found from sector 42'));

    await act(async () => {
      root.render(<RoutePlannerPanel />);
    });
    await expandPanel();

    const form = container.querySelector('.route-planner-form') as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });

    expect(container.querySelector('.route-planner-error')?.textContent).toContain(
      'No viable balanced route found from sector 42'
    );
    expect(container.querySelector('.route-planner-result')).toBeNull();
    expect(container.querySelector('.route-planner-hop')).toBeNull();
  });

  describe('Recent Plans strip', () => {
    const FAKE_HISTORY = [
      {
        id: 'run-1',
        objective: 'profit',
        start_sector: '42',
        end_sector: null,
        sectors: ['42', '17', '9'],
        total_profit: 900,
        total_distance: 2,
        total_time_hours: 2.5,
        cargo_efficiency: 0.6,
        route_confidence: 0.8,
        status: 'completed',
        created_at: '2026-07-01T12:00:00Z',
      },
    ];

    const expandHistory = async () => {
      const header = container.querySelector('.route-planner-history-header') as HTMLElement;
      await act(async () => {
        header.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      });
    };

    it('renders collapsed and does not fetch until expanded, so it never displaces the Plot Route form', async () => {
      await act(async () => {
        root.render(<RoutePlannerPanel />);
      });
      await expandPanel();

      expect(container.querySelector('.route-planner-history-body')).toBeNull();
      expect(mockGetHistory).not.toHaveBeenCalled();
      // The form is still present and not pushed out by an unrequested fetch.
      expect(container.querySelector('.route-planner-form')).not.toBeNull();
    });

    it('fetches on first expand and renders an honest empty state with no rows', async () => {
      mockGetHistory.mockResolvedValue([]);

      await act(async () => {
        root.render(<RoutePlannerPanel />);
      });
      await expandPanel();
      await expandHistory();

      expect(mockGetHistory).toHaveBeenCalledTimes(1);
      expect(container.querySelector('.route-planner-history-status')?.textContent).toBe(
        'No route plans recorded yet.'
      );
      expect(container.querySelector('.route-planner-history-entry')).toBeNull();
    });

    it('does not re-fetch on subsequent collapse/expand toggles', async () => {
      mockGetHistory.mockResolvedValue(FAKE_HISTORY);

      await act(async () => {
        root.render(<RoutePlannerPanel />);
      });
      await expandPanel();
      await expandHistory();
      await expandHistory(); // collapse
      await expandHistory(); // expand again

      expect(mockGetHistory).toHaveBeenCalledTimes(1);
    });

    it('renders recorded rows and selecting one shows it clearly labeled as a past result, not live', async () => {
      mockGetHistory.mockResolvedValue(FAKE_HISTORY);

      await act(async () => {
        root.render(<RoutePlannerPanel />);
      });
      await expandPanel();
      await expandHistory();

      const entryButton = container.querySelector('.route-planner-history-entry') as HTMLButtonElement;
      expect(entryButton).not.toBeNull();

      await act(async () => {
        entryButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      });

      const badge = container.querySelector('.route-planner-history-badge');
      expect(badge?.textContent).toContain('Past result');

      const hops = Array.from(container.querySelectorAll('.route-planner-hop')).map(h => h.textContent);
      expect(hops).toEqual(['42', '17', '9']);

      // No fabricated live-only fields (risk, opportunities table) for a
      // historical entry -- the run-log never recorded them.
      const statLabels = Array.from(container.querySelectorAll('.route-planner-stat .stat-label')).map(
        (el) => el.textContent
      );
      expect(statLabels).not.toContain('Risk');
      expect(container.querySelector('.route-planner-opportunities')).toBeNull();
    });

    it('shows a visible error and no rows when the history fetch rejects', async () => {
      mockGetHistory.mockRejectedValue(new Error('Failed to load route history: 500'));

      await act(async () => {
        root.render(<RoutePlannerPanel />);
      });
      await expandPanel();
      await expandHistory();

      expect(container.querySelector('.route-planner-history-body .route-planner-error')?.textContent).toContain(
        'Failed to load route history: 500'
      );
      expect(container.querySelector('.route-planner-history-entry')).toBeNull();
    });
  });
});

describe('RoutePlannerPanel TypeError densify (LEG-3264)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockOptimizeRoute.mockReset();
    mockGetHistory.mockReset();
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
    vi.clearAllMocks();
  });

  const expandPanel = async () => {
    const header = container.querySelector('.route-planner-header') as HTMLElement;
    await act(async () => {
      header.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
  };

  it('formatRouteOptimizeError falls back on TypeError network collapse', () => {
    const text = formatRouteOptimizeError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to optimize route.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatRouteHistoryError falls back on TypeError network collapse', () => {
    const text = formatRouteHistoryError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to load recent plans.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatRouteOptimize/History fall back on axios Network Error / Failed to fetch (LEG-3292)', () => {
    expect(formatRouteOptimizeError(new Error('Network Error'))).toBe('Failed to optimize route.');
    expect(formatRouteOptimizeError(new Error('Failed to fetch'))).toBe('Failed to optimize route.');
    expect(formatRouteOptimizeError(new Error('   '))).toBe('Failed to optimize route.');
    expect(formatRouteHistoryError(new Error('Network Error'))).toBe('Failed to load recent plans.');
    expect(formatRouteHistoryError(new Error('Failed to fetch'))).toBe('Failed to load recent plans.');
    expect(formatRouteOptimizeError(new Error('sector unreachable'))).toBe('sector unreachable');
  });

  it('optimize TypeError surfaces fallback without Failed to fetch / TypeError in DOM', async () => {
    mockOptimizeRoute.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(<RoutePlannerPanel />);
    });
    await expandPanel();

    const form = container.querySelector('.route-planner-form') as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });

    const err = container.querySelector('.route-planner-error');
    expect(err?.textContent).toContain('Failed to optimize route.');
    expect(err?.textContent).not.toMatch(/Failed to fetch/i);
    expect(err?.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });

  it('history TypeError surfaces fallback without Failed to fetch / TypeError in DOM', async () => {
    mockGetHistory.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(<RoutePlannerPanel />);
    });
    await expandPanel();

    const historyHeader = container.querySelector('.route-planner-history-header') as HTMLElement;
    await act(async () => {
      historyHeader.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    const err = container.querySelector('.route-planner-history-body .route-planner-error');
    expect(err?.textContent).toContain('Failed to load recent plans.');
    expect(err?.textContent).not.toMatch(/Failed to fetch/i);
    expect(err?.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });
});
