// @vitest-environment jsdom
/**
 * TradingInterface — resource card icon wiring (WO-ARCH-RES-3B-PC-RESIDUAL-
 * LITERALS, accept 1-2).
 *
 * The retired local getResourceIcon() keyed off Capitalized names
 * ('Fuel', 'Ore', ...) that marketInfo.resources never actually carries —
 * trading.py serializes lowercase wire slugs — so every card silently fell
 * to the generic 📦 fallback in production. This asserts each resource card
 * now renders the shared resourceCatalog glyph for its lowercase key, and
 * that an unrecognized key still degrades to 📦 rather than throwing.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

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
    ore: RESOURCE(),
    organics: RESOURCE(),
    equipment: RESOURCE(),
    fuel: RESOURCE(),
    luxury_goods: RESOURCE(),
    gourmet_food: RESOURCE(),
    exotic_technology: RESOURCE(),
    colonists: RESOURCE(),
    mystery_goo: RESOURCE(),
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
    stationsInSector: [],
    isLoading: false,
    error: null,
  }),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ addNotification: vi.fn(), isConnected: true }),
}));

import TradingInterface from '../TradingInterface';

describe('TradingInterface — resource card icons', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
    vi.clearAllMocks();
  });

  it('renders the shared-catalog glyph for every known lowercase wire key, and 📦 for an unknown key — zero cards fall to the generic default for a known key', async () => {
    await act(async () => {
      root.render(<TradingInterface onClose={vi.fn()} />);
    });

    const cards = Array.from(container.querySelectorAll('.resource-card'));

    const iconFor = (key: string): string | undefined => {
      const card = cards.find((c) => c.querySelector('.resource-name')?.textContent === formatNameOf(key));
      return card?.querySelector('.resource-icon')?.textContent || undefined;
    };
    // formatName mirrors TradingInterface's local prettifier so the lookup
    // above matches the rendered .resource-name text.
    function formatNameOf(name: string) {
      return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
    }

    expect(iconFor('ore')).toBe('⛏️');
    expect(iconFor('organics')).toBe('🌿');
    expect(iconFor('equipment')).toBe('⚙️');
    expect(iconFor('fuel')).toBe('⛽');
    expect(iconFor('luxury_goods')).toBe('💎');
    expect(iconFor('gourmet_food')).toBe('🍽️');
    expect(iconFor('exotic_technology')).toBe('🔬');
    expect(iconFor('colonists')).toBe('👥');
    expect(iconFor('mystery_goo')).toBe('📦');

    // The dead Capitalized-keyed map is gone — no card silently falls to the
    // generic icon for a resource the shared catalog actually knows.
    const knownIcons = ['⛏️', '🌿', '⚙️', '⛽', '💎', '🍽️', '🔬', '👥'];
    const genericCount = cards.filter((c) => c.querySelector('.resource-icon')?.textContent === '📦').length;
    expect(genericCount).toBe(1); // only mystery_goo
    expect(knownIcons.every((icon) => cards.some((c) => c.querySelector('.resource-icon')?.textContent === icon))).toBe(true);
  });
});
