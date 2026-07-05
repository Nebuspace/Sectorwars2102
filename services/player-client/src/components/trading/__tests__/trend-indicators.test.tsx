// @vitest-environment jsdom
/**
 * TradingInterface — price-trend glyphs + expandable sparkline
 * (WO-ECON-MKT-TIMESERIES).
 *
 * marketInfo.resources now carries price_trend (computed on every reprice by
 * TradingService.update_market_prices, surfaced by GET /trading/market/{id}
 * per this WO's api lane); this pins the up/down/flat glyph direction the UI
 * derives from it, and the expandable per-commodity sparkline fed by the new
 * GET /trading/market/{id}/history endpoint — including its graceful
 * "no history yet" state for an empty response.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { mockGet } = vi.hoisted(() => ({ mockGet: vi.fn() }));

const RESOURCE = (overrides: Partial<Record<string, unknown>> = {}) => ({
  quantity: 100,
  buy_price: 10,
  sell_price: 12,
  station_buys: false,
  station_sells: true,
  ...overrides,
});

const MARKET_INFO = {
  port: { name: 'Test Station', type: 'trading_post', station_class: 'CLASS_1', tax_rate: 0.1 },
  resources: {
    // > +0.5% epsilon
    ore: RESOURCE({ price_trend: 0.02 }),
    // < -0.5% epsilon
    organics: RESOURCE({ price_trend: -0.03 }),
    // inside the +/-0.5% epsilon band
    fuel: RESOURCE({ price_trend: 0.001 }),
    // no trend data at all (pre-sweep / never repriced)
    equipment: RESOURCE({}),
  },
};

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: { is_docked: true, credits: 5000 },
    currentShip: { cargo: { contents: {} } },
    marketInfo: MARKET_INFO,
    getMarketInfo: vi.fn(),
    buyResource: vi.fn(),
    sellResource: vi.fn(),
    dockAtStation: vi.fn(),
    getStationSlips: vi.fn(),
    bumpDockOccupant: vi.fn(),
    // A single station so the auto-select effect populates selectedPort —
    // the sparkline fetch is keyed off it.
    stationsInSector: [{ id: 'station-1', name: 'Test Station', type: 'trading_post' }],
    isLoading: false,
    error: null,
  }),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ addNotification: vi.fn(), isConnected: true }),
}));

vi.mock('../../../services/apiClient', () => ({
  default: { get: mockGet },
}));

import TradingInterface from '../TradingInterface';

const cardFor = (container: HTMLElement, resourceType: string): HTMLElement => {
  const formatNameOf = (name: string) => name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  const cards = Array.from(container.querySelectorAll('.resource-card'));
  const card = cards.find((c) => c.querySelector('.resource-name')?.textContent === formatNameOf(resourceType));
  if (!card) throw new Error(`no resource-card rendered for ${resourceType}`);
  return card as HTMLElement;
};

describe('TradingInterface — price-trend glyphs + sparkline', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockGet.mockReset();
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
    vi.clearAllMocks();
  });

  it('renders an up arrow for a trend past the positive epsilon', async () => {
    await act(async () => { root.render(<TradingInterface onClose={vi.fn()} />); });

    const glyph = cardFor(container, 'ore').querySelector('.trend-glyph') as HTMLElement;
    expect(glyph.textContent).toBe('▲');
    expect(glyph.className).toContain('up');
    expect(glyph.getAttribute('aria-label')).toContain('rising');
  });

  it('renders a down arrow for a trend past the negative epsilon', async () => {
    await act(async () => { root.render(<TradingInterface onClose={vi.fn()} />); });

    const glyph = cardFor(container, 'organics').querySelector('.trend-glyph') as HTMLElement;
    expect(glyph.textContent).toBe('▼');
    expect(glyph.className).toContain('down');
    expect(glyph.getAttribute('aria-label')).toContain('falling');
  });

  it('renders a flat dash for a trend inside the epsilon band', async () => {
    await act(async () => { root.render(<TradingInterface onClose={vi.fn()} />); });

    const glyph = cardFor(container, 'fuel').querySelector('.trend-glyph') as HTMLElement;
    expect(glyph.textContent).toBe('–');
    expect(glyph.className).toContain('flat');
  });

  it('renders a flat dash when no trend data exists at all (pre-sweep)', async () => {
    await act(async () => { root.render(<TradingInterface onClose={vi.fn()} />); });

    const glyph = cardFor(container, 'equipment').querySelector('.trend-glyph') as HTMLElement;
    expect(glyph.textContent).toBe('–');
    expect(glyph.className).toContain('flat');
    expect(glyph.getAttribute('aria-label')).toContain('No trend data');
  });

  it('fetches and renders an n-point sparkline when the toggle is expanded', async () => {
    mockGet.mockResolvedValue({
      data: {
        history: [
          { snapshot_date: '2026-06-30T00:00:00Z', snapshot_type: 'hourly', buy_price: 8, sell_price: 10, quantity: 40 },
          { snapshot_date: '2026-06-30T01:00:00Z', snapshot_type: 'hourly', buy_price: 9, sell_price: 11, quantity: 45 },
          { snapshot_date: '2026-06-30T02:00:00Z', snapshot_type: 'hourly', buy_price: 10, sell_price: 12, quantity: 50 },
        ],
      },
    });

    await act(async () => { root.render(<TradingInterface onClose={vi.fn()} />); });

    const toggle = cardFor(container, 'ore').querySelector('.sparkline-toggle') as HTMLElement;
    await act(async () => {
      toggle.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    // Let the history-fetch effect's promise resolve and its setState commit.
    await act(async () => { await Promise.resolve(); });

    expect(mockGet).toHaveBeenCalledWith(
      '/api/v1/trading/market/station-1/history',
      expect.objectContaining({ params: expect.objectContaining({ commodity: 'ore' }) })
    );

    const polyline = cardFor(container, 'ore').querySelector('.price-sparkline-panel polyline');
    expect(polyline).not.toBeNull();
    const points = (polyline!.getAttribute('points') || '').trim().split(/\s+/);
    expect(points).toHaveLength(3);
  });

  it('shows a graceful "no history yet" state for an empty history response', async () => {
    mockGet.mockResolvedValue({ data: { history: [] } });

    await act(async () => { root.render(<TradingInterface onClose={vi.fn()} />); });

    const toggle = cardFor(container, 'organics').querySelector('.sparkline-toggle') as HTMLElement;
    await act(async () => {
      toggle.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await act(async () => { await Promise.resolve(); });

    const panel = cardFor(container, 'organics').querySelector('.price-sparkline-panel');
    expect(panel?.textContent).toContain('No history yet');
    expect(panel?.querySelector('polyline')).toBeNull();
  });

  it('toggling the sparkline does not also select the resource / open the trade modal', async () => {
    mockGet.mockResolvedValue({ data: { history: [] } });

    await act(async () => { root.render(<TradingInterface onClose={vi.fn()} />); });

    const toggle = cardFor(container, 'ore').querySelector('.sparkline-toggle') as HTMLElement;
    await act(async () => {
      toggle.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    expect(container.querySelector('.trade-modal')).toBeNull();
  });
});
