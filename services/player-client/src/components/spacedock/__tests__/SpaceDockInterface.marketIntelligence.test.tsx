// @vitest-environment jsdom
/**
 * SpaceDockInterface — docked ARIA market-intelligence GET (LEG-1937).
 *
 * Hub service icon was display-only. This pins click → GET
 * /api/v1/ai/market-intelligence/{station_id} and honest empty predictions.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockGetMarketIntelligence } = vi.hoisted(() => ({
  mockGetMarketIntelligence: vi.fn(),
}));

function makeStation(overrides: Record<string, unknown> = {}) {
  return {
    id: 'station-1',
    name: 'Trading Post',
    type: 'TRADING',
    sector_id: 100,
    services: { market_intelligence: true },
    status: 'OPERATIONAL',
    ...overrides,
  };
}

function makeGameState(station: unknown) {
  return {
    playerState: {
      id: 'player-1',
      credits: 1000,
      current_port_id: 'station-1',
      is_docked: true,
    },
    stationsInSector: [station],
    updatePlayerCredits: vi.fn(),
    updateShipGenesis: vi.fn(),
    refreshPlayerState: vi.fn().mockResolvedValue(undefined),
    loadShips: vi.fn(),
    getStationSlips: vi.fn().mockResolvedValue(null),
  };
}

let gameState: ReturnType<typeof makeGameState>;
vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => gameState,
}));

vi.mock('../../../services/api', async () => {
  const actual = await vi.importActual<typeof import('../../../services/api')>(
    '../../../services/api',
  );
  return {
    ...actual,
    ariaMarketAPI: {
      getMarketIntelligence: (...a: unknown[]) => mockGetMarketIntelligence(...a),
    },
  };
});

import SpaceDockInterface from '../SpaceDockInterface';

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('SpaceDockInterface — market intelligence (LEG-1937)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    mockGetMarketIntelligence.mockReset();
    gameState = makeGameState(makeStation());
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    errorSpy.mockRestore();
  });

  it('does not render the action when the station lacks market_intelligence', async () => {
    gameState = makeGameState(makeStation({ services: {} }));
    await act(async () => {
      root.render(<SpaceDockInterface />);
    });
    expect(container.querySelector('[data-testid="market-intelligence-service"]')).toBeNull();
  });

  it('GET success lists a prediction and an honest under-5 observation row', async () => {
    mockGetMarketIntelligence.mockResolvedValue({
      station_id: 'station-1',
      items: [
        {
          commodity: 'equipment',
          observation_count: 23,
          average_price: 1240,
          price_band: 50,
          next_prediction: 1240,
          prediction_confidence: 0.8,
        },
        {
          commodity: 'fuel',
          observation_count: 2,
          average_price: 100,
          price_band: null,
          next_prediction: null,
          prediction_confidence: null,
        },
      ],
    });
    await act(async () => {
      root.render(<SpaceDockInterface />);
    });
    const btn = container.querySelector(
      '[data-testid="market-intelligence-service"]',
    ) as HTMLButtonElement;
    expect(btn).toBeTruthy();
    await act(async () => {
      btn.click();
      await flush();
      await flush();
    });
    expect(mockGetMarketIntelligence).toHaveBeenCalledWith('station-1');
    const equipment = container.querySelector('[data-testid="market-intel-row-equipment"]');
    const fuel = container.querySelector('[data-testid="market-intel-row-fuel"]');
    expect(equipment?.textContent).toContain('I expect equipment to trade around 1240±50 credits');
    expect(equipment?.textContent).toContain('23 observations');
    expect(fuel?.textContent).toContain('not enough data for a prediction');
    expect(fuel?.textContent).not.toContain('I expect fuel');
  });

  it('surfaces 403 Error.message in the panel alert', async () => {
    const err = new Error('Must be docked at this station to view market intelligence.');
    (err as { status?: number }).status = 403;
    mockGetMarketIntelligence.mockRejectedValue(err);
    await act(async () => {
      root.render(<SpaceDockInterface />);
    });
    const btn = container.querySelector(
      '[data-testid="market-intelligence-service"]',
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
      await flush();
    });
    const alert = container.querySelector('[data-testid="market-intelligence-error"]');
    expect(alert?.getAttribute('role')).toBe('alert');
    expect(alert?.textContent).toBe('Must be docked at this station to view market intelligence.');
  });
});
