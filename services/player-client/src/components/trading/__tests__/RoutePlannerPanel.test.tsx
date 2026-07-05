// @vitest-environment jsdom
/**
 * RoutePlannerPanel — first player consumer of POST /api/v1/routes/optimize
 * (WO-SB-RO2 Lane C).
 *
 * routeOptimizerService is mocked at the module boundary (not fetch) so this
 * pins the component's own state machine: collapsed by default, expands on
 * click, a successful optimize renders the returned sector hop chain, and a
 * rejected optimize renders a visible error state with no fabricated route.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { mockOptimizeRoute } = vi.hoisted(() => ({ mockOptimizeRoute: vi.fn() }));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: { current_sector_id: 42 },
    currentShip: { cargo_capacity: 250 },
  }),
}));

vi.mock('../../../services/routeOptimizerService', () => ({
  routeOptimizerService: { optimizeRoute: mockOptimizeRoute },
}));

import RoutePlannerPanel from '../RoutePlannerPanel';

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
    expect(container.querySelector('.route-planner-header')?.textContent).toContain('Route Planner');
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
});
