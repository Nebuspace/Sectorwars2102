// @vitest-environment jsdom
/**
 * AriaMarketPredictionPanel (LEG-375) — docked predict/all hydrate + empty/error.
 * Tip payload has no observation/visit counts — tests must not require them.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const predictAll = vi.fn();
const useGame = vi.fn();

vi.mock('../../../services/api', () => ({
  marketPredictionAPI: {
    predictAll: (...args: unknown[]) => predictAll(...args),
  },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => useGame(),
}));

import AriaMarketPredictionPanel from '../AriaMarketPredictionPanel';

const tipRow = {
  commodity: 'equipment',
  station_id: 'st-1',
  current_price: 1200,
  predicted_price: 1240,
  price_change_pct: 3.3,
  trend: 'rising',
  confidence: 0.72,
  volatility: 0.1,
  lower_bound: 1190,
  upper_bound: 1290,
  prediction_horizon_hours: 4,
  factors: ['moving_average'],
  timestamp: '2026-08-17T14:00:00Z',
};

describe('AriaMarketPredictionPanel', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    predictAll.mockReset();
    useGame.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it('renders nothing and does not fetch when not docked', async () => {
    useGame.mockReturnValue({
      playerState: { is_docked: false, current_port_id: undefined },
    });
    await act(async () => {
      root.render(<AriaMarketPredictionPanel />);
    });
    expect(predictAll).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="aria-market-prediction"]')).toBeNull();
  });

  it('shows tip prediction line when docked with payload', async () => {
    useGame.mockReturnValue({
      playerState: { is_docked: true, current_port_id: 'st-1' },
    });
    predictAll.mockResolvedValue([tipRow]);
    await act(async () => {
      root.render(<AriaMarketPredictionPanel />);
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(predictAll).toHaveBeenCalledWith({ stationId: 'st-1' });
    const line = container.querySelector('[data-testid="amp-line"]')?.textContent ?? '';
    expect(line).toMatch(/Equipment/i);
    expect(line).toMatch(/1240/);
    expect(line).toMatch(/±50/);
    expect(line).toMatch(/4 hours/);
    expect(line).toMatch(/72%/);
    // Honesty: tip has no observation/visit counts — must not invent them
    expect(line).not.toMatch(/observation/i);
    expect(line).not.toMatch(/visit/i);
  });

  it('shows honest empty when predict/all returns []', async () => {
    useGame.mockReturnValue({
      playerState: { is_docked: true, current_port_id: 'st-1' },
    });
    predictAll.mockResolvedValue([]);
    await act(async () => {
      root.render(<AriaMarketPredictionPanel />);
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="amp-empty"]')?.textContent).toMatch(
      /Not enough market signal/i,
    );
  });

  it('shows error demotion when fetch fails', async () => {
    useGame.mockReturnValue({
      playerState: { is_docked: true, current_port_id: 'st-1' },
    });
    predictAll.mockRejectedValue(new Error('API Error: 503'));
    await act(async () => {
      root.render(<AriaMarketPredictionPanel />);
    });
    await act(async () => {
      await Promise.resolve();
    });
    const err = container.querySelector('[data-testid="amp-error"]');
    expect(err?.getAttribute('role')).toBe('alert');
    expect(err?.textContent).toContain('API Error: 503');
  });
});
