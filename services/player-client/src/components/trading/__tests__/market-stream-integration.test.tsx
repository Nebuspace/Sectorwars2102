// @vitest-environment jsdom
/**
 * TradingInterface <-> marketStreamService integration (WO-RT-MARKET-
 * STREAM-CLIENT). Mirrors trend-indicators.test.tsx's dependency-free
 * pattern — raw react-dom/client + act(), no RTL/new deps — to pin:
 *
 *  1. Docking subscribes to exactly the commodities the port trades.
 *  2. A mocked market_update delta repaints the affected price cell and
 *     triggers a brief flash class.
 *  3. Unmounting (undock) tears the subscription down — disconnect() called.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { MarketStreamUpdateMessage } from '../../../services/marketStream';

const RESOURCE = (overrides: Partial<Record<string, unknown>> = {}) => ({
  quantity: 100,
  buy_price: 10,
  sell_price: 12,
  station_buys: true,
  station_sells: true,
  ...overrides,
});

const MARKET_INFO = {
  port: { name: 'Test Station', type: 'trading_post', station_class: 'CLASS_1', tax_rate: 0.1 },
  resources: {
    ore: RESOURCE(),
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
    stationsInSector: [{ id: 'station-1', name: 'Test Station', type: 'trading_post' }],
    isLoading: false,
    error: null,
  }),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ addNotification: vi.fn(), isConnected: true }),
}));

vi.mock('../../../services/apiClient', () => ({
  default: { get: vi.fn() },
}));

const connectMock = vi.fn();
const disconnectMock = vi.fn();
let capturedHandler: ((message: MarketStreamUpdateMessage) => void) | null = null;
const unsubscribeMock = vi.fn();

vi.mock('../../../services/marketStream', () => ({
  default: {
    connect: (...args: unknown[]) => connectMock(...args),
    disconnect: (...args: unknown[]) => disconnectMock(...args),
    onUpdate: (handler: (message: MarketStreamUpdateMessage) => void) => {
      capturedHandler = handler;
      return unsubscribeMock;
    },
    onStatus: vi.fn(() => vi.fn()),
    isConnected: vi.fn(() => false),
  },
}));

import TradingInterface from '../TradingInterface';

const cardFor = (container: HTMLElement, resourceType: string): HTMLElement => {
  const formatNameOf = (name: string) => name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  const cards = Array.from(container.querySelectorAll('.resource-card'));
  const card = cards.find((c) => c.querySelector('.resource-name')?.textContent === formatNameOf(resourceType));
  if (!card) throw new Error(`no resource-card rendered for ${resourceType}`);
  return card as HTMLElement;
};

describe('TradingInterface <-> marketStreamService', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    connectMock.mockClear();
    disconnectMock.mockClear();
    unsubscribeMock.mockClear();
    capturedHandler = null;
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
    vi.clearAllMocks();
  });

  it('subscribes to exactly the docked port commodity set on mount', async () => {
    await act(async () => { root.render(<TradingInterface onClose={vi.fn()} />); });

    expect(connectMock).toHaveBeenCalledWith(['ore']);
    expect(capturedHandler).not.toBeNull();
  });

  it('a market_update delta repaints the price cell and flashes', async () => {
    vi.useFakeTimers();
    try {
      await act(async () => { root.render(<TradingInterface onClose={vi.fn()} />); });

      expect(cardFor(container, 'ore').querySelector('.sell-price')?.textContent).toContain('10');

      act(() => {
        capturedHandler?.({
          type: 'market_update',
          commodity: 'ore',
          data: { station_id: 'station-1', buy_price: 20, sell_price: 12 },
          timestamp: '2026-07-09T00:00:00Z',
        });
      });

      const priceBlock = cardFor(container, 'ore').querySelector('.resource-prices') as HTMLElement;
      expect(priceBlock.querySelector('.sell-price')?.textContent).toContain('20');
      expect(priceBlock.className).toContain('price-flash-up');

      // The flash clears itself after its timeout.
      act(() => { vi.advanceTimersByTime(1000); });
      expect(priceBlock.className).not.toContain('price-flash-up');
    } finally {
      vi.useRealTimers();
    }
  });

  it('a market_update for a different station is ignored', async () => {
    await act(async () => { root.render(<TradingInterface onClose={vi.fn()} />); });

    act(() => {
      capturedHandler?.({
        type: 'market_update',
        commodity: 'ore',
        data: { station_id: 'some-other-station', buy_price: 999 },
        timestamp: '2026-07-09T00:00:00Z',
      });
    });

    expect(cardFor(container, 'ore').querySelector('.sell-price')?.textContent).toContain('10');
  });

  it('unmounting (undock) tears the subscription down', async () => {
    await act(async () => { root.render(<TradingInterface onClose={vi.fn()} />); });
    expect(connectMock).toHaveBeenCalledTimes(1);

    await act(async () => { root.unmount(); });

    expect(unsubscribeMock).toHaveBeenCalled();
    expect(disconnectMock).toHaveBeenCalled();
  });
});
