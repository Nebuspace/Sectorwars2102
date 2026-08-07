// @vitest-environment jsdom
/**
 * TradingInterface — Scroll Law DOM order (WO-SCROLL-TRADE-ROUTE-PLANNER-BELOW).
 *
 * The route planner is secondary chrome. It must mount AFTER `.trading-content`
 * so the buy/sell desk clears the fold at 1440×900 even when the planner
 * header is visible (collapsed).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: { is_docked: true, credits: 5000 },
    currentShip: { cargo: { contents: {} } },
    marketInfo: {
      port: { name: 'Test Station', type: 'trading_post', station_class: 'CLASS_1', tax_rate: 0.1 },
      resources: {
        ore: {
          quantity: 100,
          buy_price: 10,
          sell_price: 12,
          station_buys: false,
          station_sells: true,
        },
      },
    },
    getMarketInfo: vi.fn(),
    buyResource: vi.fn(),
    sellResource: vi.fn(),
    dockAtStation: vi.fn(),
    getStationSlips: vi.fn(),
    bumpDockOccupant: vi.fn(),
    stationsInSector: [],
    isLoading: false,
    error: null,
  }),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ addNotification: vi.fn(), isConnected: true }),
}));

vi.mock('../../../services/routeOptimizerService', () => ({
  routeOptimizerService: { optimizeRoute: vi.fn(), getHistory: vi.fn() },
}));

import TradingInterface from '../TradingInterface';

describe('TradingInterface — Scroll Law (route planner below desk)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
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

  it('mounts .route-planner-panel after .trading-content (buy/sell desk first)', async () => {
    await act(async () => {
      root.render(<TradingInterface />);
    });

    const iface = container.querySelector('.trading-interface') as HTMLElement;
    expect(iface).not.toBeNull();
    const content = iface.querySelector('.trading-content') as HTMLElement;
    const planner = iface.querySelector('.route-planner-panel') as HTMLElement;
    expect(content).not.toBeNull();
    expect(planner).not.toBeNull();

    const position = content.compareDocumentPosition(planner);
    expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(position & Node.DOCUMENT_POSITION_PRECEDING).toBeFalsy();
  });
});
