// @vitest-environment jsdom
/**
 * TradingInterface — buy/sell confirm depth (WO-TESTCOV-PLAYER-TRADING-INTERFACE-DEPTH).
 *
 * useGame mock MUST return stable object identities — fresh literals each call
 * re-fire the tradeCalculation effect (deps include playerState/currentShip)
 * and infinite-loop the suite once a quote is in flight.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const {
  buyResource,
  sellResource,
  apiPost,
  getMarketInfo,
  PLAYER,
  SHIP,
  MARKET,
  STATIONS,
  WS,
} = vi.hoisted(() => {
  const buyResource = vi.fn();
  const sellResource = vi.fn();
  const apiPost = vi.fn();
  const getMarketInfo = vi.fn();
  return {
    buyResource,
    sellResource,
    apiPost,
    getMarketInfo,
    PLAYER: { is_docked: true, credits: 50000 },
    SHIP: {
      cargo: { contents: { ore: 20 }, capacity: 200 },
      cargo_capacity: 200,
    },
    MARKET: {
      port: { name: 'Test Station', type: 'trading_post', station_class: 'CLASS_1', tax_rate: 0.1 },
      resources: {
        ore: {
          quantity: 100,
          buy_price: 10,
          sell_price: 12,
          station_buys: true,
          station_sells: true,
        },
      },
    },
    STATIONS: [{ id: 'station-1', name: 'Test Station', type: 'trading_post' }],
    WS: { addNotification: vi.fn(), isConnected: true },
  };
});

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: PLAYER,
    currentShip: SHIP,
    marketInfo: MARKET,
    getMarketInfo,
    buyResource: (...a: unknown[]) => buyResource(...a),
    sellResource: (...a: unknown[]) => sellResource(...a),
    dockAtStation: vi.fn(),
    getStationSlips: vi.fn(),
    bumpDockOccupant: vi.fn(),
    stationsInSector: STATIONS,
    isLoading: false,
    error: null,
  }),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => WS,
}));

vi.mock('../../../services/apiClient', () => ({
  default: {
    get: vi.fn().mockResolvedValue({ data: [] }),
    post: (...a: unknown[]) => apiPost(...a),
  },
}));

vi.mock('../../../services/marketStream', () => ({
  default: {
    connect: vi.fn(),
    disconnect: vi.fn(),
    onUpdate: vi.fn(() => vi.fn()),
    onStatus: vi.fn(() => vi.fn()),
    isConnected: vi.fn(() => false),
  },
}));

vi.mock('../../../services/routeOptimizerService', () => ({
  routeOptimizerService: { optimizeRoute: vi.fn(), getHistory: vi.fn() },
}));

import TradingInterface from '../TradingInterface';

function quoteOk(action: 'buy' | 'sell', quantity = 1) {
  const unit = action === 'buy' ? 12 : 10;
  const subtotal = unit * quantity;
  const tax = Math.floor(subtotal * 0.1);
  return {
    data: {
      resource_type: 'ore',
      quantity,
      action,
      unit_price: unit,
      subtotal,
      tax_rate: 0.1,
      tax,
      total: action === 'buy' ? subtotal + tax : subtotal - tax,
    },
  };
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe('TradingInterface — buy/sell confirm depth', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    buyResource.mockReset().mockResolvedValue({ success: true });
    sellResource.mockReset().mockResolvedValue({ success: true });
    apiPost.mockReset().mockImplementation((_u: string, body: { action: 'buy' | 'sell'; quantity: number }) =>
      Promise.resolve(quoteOk(body?.action ?? 'buy', body?.quantity ?? 1)),
    );
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    document.body.querySelectorAll('.trade-modal-overlay').forEach((n) => n.remove());
  });

  it('mounts docked trading desk', async () => {
    await act(async () => {
      root.render(<TradingInterface onClose={() => undefined} />);
    });
    expect(container.querySelector('.trading-interface')).toBeTruthy();
    expect(container.textContent).toMatch(/Ore/i);
  });

  it('opens the trade modal and requests a buy quote for ore', async () => {
    await act(async () => {
      root.render(<TradingInterface onClose={() => undefined} />);
    });

    const card = Array.from(container.querySelectorAll('.resource-card')).find((c) =>
      /Ore/i.test(c.textContent || ''),
    );
    expect(card).toBeTruthy();
    await act(async () => {
      card!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    await sleep(350);
    await act(async () => {
      await Promise.resolve();
    });

    expect(document.body.querySelector('button.confirm-trade-btn')).toBeTruthy();
    expect(apiPost).toHaveBeenCalledWith(
      '/api/v1/trading/quote',
      expect.objectContaining({
        station_id: 'station-1',
        resource_type: 'ore',
        quantity: 1,
        action: 'buy',
      }),
    );
  });

  it('sells ore via Confirm in sell mode', async () => {
    await act(async () => {
      root.render(<TradingInterface onClose={() => undefined} />);
    });

    const sellMode = Array.from(container.querySelectorAll('button.mode-button')).find((b) =>
      /sell/i.test(b.textContent || ''),
    );
    expect(sellMode).toBeTruthy();
    await act(async () => {
      sellMode!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    const card = Array.from(container.querySelectorAll('.resource-card')).find((c) =>
      /Ore/i.test(c.textContent || ''),
    );
    await act(async () => {
      card!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await sleep(350);
    await act(async () => {
      await Promise.resolve();
    });

    await vi.waitFor(() => {
      const btn = document.body.querySelector('button.confirm-trade-btn') as HTMLButtonElement | null;
      expect(btn).toBeTruthy();
      expect(btn!.disabled).toBe(false);
    }, { timeout: 2000, interval: 50 });

    const confirm = document.body.querySelector('button.confirm-trade-btn')!;

    await act(async () => {
      confirm.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await sleep(50);
    await act(async () => {
      await Promise.resolve();
    });

    expect(sellResource).toHaveBeenCalledWith('station-1', 'ore', 1);
  });
});
