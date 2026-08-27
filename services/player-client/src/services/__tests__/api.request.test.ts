/**
 * api.ts apiRequest error shaping + money/combat/trade wrappers
 * (WO-TESTCOV-PLAYER-API-CLIENT).
 *
 * apiRequest prefers verb helpers (get/post/put/delete); .request is only
 * used when a timeout is set. Mocks must match that axios surface.
 */
import { AxiosError } from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../apiClient', () => ({
  default: {
    request: vi.fn(),
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

import apiClient from '../apiClient';
import { centralBankAPI, citadelAPI, combatAPI, greyStatusAPI, miningAPI, navAPI, planetaryAPI, playerAPI, portOwnershipAPI, sectorAPI, shipRegistryAPI, tradeAPI } from '../api';

const get = apiClient.get as ReturnType<typeof vi.fn>;
const post = apiClient.post as ReturnType<typeof vi.fn>;

function axiosHttpError(status: number, data: unknown): AxiosError {
  return new AxiosError(
    `Request failed with status code ${status}`,
    String(status),
    undefined,
    undefined,
    {
      data,
      status,
      statusText: '',
      headers: {},
      config: {} as never,
    },
  );
}

const jsonHeaders = expect.objectContaining({
  headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
});

describe('apiRequest via trade/combat/grey wrappers', () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
  });

  it('tradeAPI.initiate POSTs target_player_id and returns body', async () => {
    post.mockResolvedValue({ data: { id: 'sess-1' } });
    const out = await tradeAPI.initiate('player-9');
    expect(out).toEqual({ id: 'sess-1' });
    expect(post).toHaveBeenCalledWith(
      '/api/v1/trade/initiate',
      JSON.stringify({ target_player_id: 'player-9' }),
      jsonHeaders,
    );
  });

  it('tradeAPI.offer defaults empty offer fields', async () => {
    post.mockResolvedValue({ data: { ok: true } });
    await tradeAPI.offer('s1', { credits: 50 });
    expect(post).toHaveBeenCalledWith(
      '/api/v1/trade/s1/offer',
      JSON.stringify({
        credits: 50,
        commodities: {},
        ship_id: null,
        ships: [],
      }),
      jsonHeaders,
    );
  });

  it('combatAPI.engage posts targetType/targetId', async () => {
    post.mockResolvedValue({ data: { combatId: 'c1' } });
    await expect(combatAPI.engage('port', 'port-1')).resolves.toEqual({
      combatId: 'c1',
    });
    expect(post).toHaveBeenCalledWith(
      '/api/v1/combat/engage',
      JSON.stringify({ targetType: 'port', targetId: 'port-1' }),
      jsonHeaders,
    );
  });

  it('combatAPI.getHistory GETs limit/offset query (LEG-372)', async () => {
    get.mockResolvedValue({
      data: { items: [], total: 0, limit: 10, offset: 5 },
    });
    await expect(combatAPI.getHistory({ limit: 10, offset: 5 })).resolves.toEqual({
      items: [],
      total: 0,
      limit: 10,
      offset: 5,
    });
    expect(get).toHaveBeenCalledWith(
      '/api/v1/combat/history?limit=10&offset=5',
      jsonHeaders,
    );
  });

  it('surfaces string detail from FastAPI errors', async () => {
    post.mockRejectedValue(axiosHttpError(400, { detail: 'not enough credits' }));
    await expect(greyStatusAPI.clearFine()).rejects.toThrow('not enough credits');
  });

  it('surfaces message when detail is absent', async () => {
    get.mockRejectedValue(axiosHttpError(500, { message: 'backend blew up' }));
    await expect(tradeAPI.get('x')).rejects.toThrow('backend blew up');
  });

  it('attaches structured detail.errors/code/regions on thrown Error', async () => {
    get.mockRejectedValue(
      axiosHttpError(400, {
        detail: {
          code: 'ERR_AMBIGUOUS_REGION_OWNER',
          errors: ['pick a region', 'still ambiguous'],
          regions: [{ id: 'r1', name: 'Alpha' }],
        },
      }),
    );
    try {
      await greyStatusAPI.getStatus();
      expect.fail('expected throw');
    } catch (e) {
      const err = e as Error & {
        errors?: string[];
        code?: string;
        regions?: Array<{ id: string; name: string }>;
      };
      expect(err.message).toBe('pick a region; still ambiguous');
      expect(err.errors).toEqual(['pick a region', 'still ambiguous']);
      expect(err.code).toBe('ERR_AMBIGUOUS_REGION_OWNER');
      expect(err.regions).toEqual([{ id: 'r1', name: 'Alpha' }]);
    }
  });

  it('falls back to API Error: <status> when body has no message', async () => {
    get.mockRejectedValue(axiosHttpError(503, {}));
    await expect(combatAPI.getStatus('c9')).rejects.toThrow('API Error: 503');
  });

  it('rethrows non-response axios failures unchanged', async () => {
    const net = new AxiosError('Network Error');
    get.mockRejectedValue(net);
    await expect(tradeAPI.getOpen()).rejects.toBe(net);
  });

  it('rethrows non-axios errors unchanged', async () => {
    const boom = new Error('adapter exploded');
    post.mockRejectedValue(boom);
    await expect(tradeAPI.accept('s')).rejects.toBe(boom);
  });

  it('shipRegistryAPI.eject POSTs with no body and no ship_id', async () => {
    post.mockResolvedValue({ data: { ejected_ship_id: 'ship-1', turns_spent: 1 } });
    const out = await shipRegistryAPI.eject();
    expect(out).toEqual({ ejected_ship_id: 'ship-1', turns_spent: 1 });
    expect(post).toHaveBeenCalledWith(
      '/api/v1/players/me/eject',
      undefined,
      jsonHeaders,
    );
  });

  it('shipRegistryAPI.board omits pin from the body when not supplied', async () => {
    post.mockResolvedValue({ data: { boarded: true, state: 'owner_aboard' } });
    await shipRegistryAPI.board('ship-2');
    expect(post).toHaveBeenCalledWith(
      '/api/v1/ships/ship-2/board',
      JSON.stringify({}),
      jsonHeaders,
    );
  });

  it('shipRegistryAPI.board includes the pin when supplied', async () => {
    post.mockResolvedValue({ data: { boarded: true, state: 'borrowed' } });
    await shipRegistryAPI.board('ship-2', 'ABC123');
    expect(post).toHaveBeenCalledWith(
      '/api/v1/ships/ship-2/board',
      JSON.stringify({ pin: 'ABC123' }),
      jsonHeaders,
    );
  });

  it('shipRegistryAPI.setPin POSTs the new pin', async () => {
    post.mockResolvedValue({ data: { ship_id: 'ship-2', hatch_pin_code: 'NEWPIN1' } });
    await shipRegistryAPI.setPin('ship-2', 'NEWPIN1');
    expect(post).toHaveBeenCalledWith(
      '/api/v1/ships/ship-2/set-pin',
      JSON.stringify({ pin: 'NEWPIN1' }),
      jsonHeaders,
    );
  });

  it('shipRegistryAPI.requestPinReset POSTs port_id and pin', async () => {
    post.mockResolvedValue({ data: { ship_id: 'ship-2', effective_at: '2026-01-01T01:00:00Z' } });
    await shipRegistryAPI.requestPinReset('ship-2', 'port-9', 'NEWPIN1');
    expect(post).toHaveBeenCalledWith(
      '/api/v1/ships/ship-2/request-pin-reset',
      JSON.stringify({ port_id: 'port-9', pin: 'NEWPIN1' }),
      jsonHeaders,
    );
  });

  it('navAPI.plot POSTs target_sector_id with default min_time objective', async () => {
    const plot = {
      success: true as const,
      reachable: true as const,
      target_sector_id: 42,
      hops: [],
      total_turns: 0,
    };
    post.mockResolvedValue({ data: plot });
    await expect(navAPI.plot(42)).resolves.toEqual(plot);
    expect(post).toHaveBeenCalledWith(
      '/api/v1/nav/plot',
      JSON.stringify({ target_sector_id: 42, objective: 'min_time' }),
      jsonHeaders,
    );
  });

  it('miningAPI.getNearestAmRefinery GETs the tip overlay path', async () => {
    const payload = {
      found: true,
      station: { id: 'st-1', name: 'AM 7', sector_id: 9 },
      hop_distance: 2,
      ore_buy_price: 11,
      reason: null,
    };
    get.mockResolvedValue({ data: payload });
    await expect(miningAPI.getNearestAmRefinery()).resolves.toEqual(payload);
    expect(get).toHaveBeenCalledWith('/api/v1/mining/nearest-am-refinery', jsonHeaders);
  });

  it('miningAPI.getYieldPreview GETs the tip yield-preview path', async () => {
    const payload = {
      success: true,
      ore_lo: 4,
      ore_hi: 9,
      richness_tier: 2,
      laser_level: 1,
      depletion_modifier: 1,
      turns_cost: 5,
    };
    get.mockResolvedValue({ data: payload });
    await expect(miningAPI.getYieldPreview('ship-9')).resolves.toEqual(payload);
    expect(get).toHaveBeenCalledWith(
      '/api/v1/mining/yield-preview?ship_id=ship-9',
      jsonHeaders,
    );
  });

  it('miningAPI.harvest POSTs ship_id', async () => {
    post.mockResolvedValue({ data: { status: 'in_progress', harvest_id: 'h1' } });
    await expect(miningAPI.harvest('ship-9')).resolves.toEqual({
      status: 'in_progress',
      harvest_id: 'h1',
    });
    expect(post).toHaveBeenCalledWith(
      '/api/v1/mining/harvest',
      JSON.stringify({ ship_id: 'ship-9' }),
      jsonHeaders,
    );
  });

  it('playerAPI.investigateFormation POSTs the formation id path', async () => {
    post.mockResolvedValue({ data: { reward_credits: 50 } });
    await expect(playerAPI.investigateFormation('f-1')).resolves.toEqual({
      reward_credits: 50,
    });
    expect(post).toHaveBeenCalledWith(
      '/api/v1/player/formations/f-1/investigate',
      undefined,
      jsonHeaders,
    );
  });

  it('sectorAPI.getContents GETs the sector contents path', async () => {
    get.mockResolvedValue({ data: { star: { label: 'Sol' }, bodies: [] } });
    await expect(sectorAPI.getContents(100)).resolves.toEqual({
      star: { label: 'Sol' },
      bodies: [],
    });
    expect(get).toHaveBeenCalledWith('/api/v1/sectors/100/contents', jsonHeaders);
  });

  it('citadelAPI.constructBuilding places via /grid/place not /buildings/construct', async () => {
    get.mockResolvedValue({
      data: {
        plots: [
          { x: 0, y: 0, cleared: false },
          { x: 1, y: 0, cleared: true, building_id: 'b_1' },
          { x: 2, y: 0, cleared: true, hazard: { kind: 'quake' } },
          { x: 3, y: 1, cleared: true },
        ],
      },
    });
    post.mockResolvedValue({ data: { success: true, building: { kind: 'TURRET_NETWORK' } } });

    const out = await citadelAPI.constructBuilding('planet-9', 'turret_network');
    expect(out).toEqual({ success: true, building: { kind: 'TURRET_NETWORK' } });
    expect(get).toHaveBeenCalledWith('/api/v1/planets/planet-9/grid', jsonHeaders);
    expect(post).toHaveBeenCalledWith(
      '/api/v1/planets/planet-9/grid/place',
      JSON.stringify({ kind: 'TURRET_NETWORK', x: 3, y: 1, level: 1 }),
      jsonHeaders,
    );
    expect(post.mock.calls.some((c) => String(c[0]).includes('/buildings/construct'))).toBe(false);
  });

  it('centralBankAPI.getBalance GETs /central-bank/balance', async () => {
    get.mockResolvedValue({ data: { credits: 500, commodities: { fuel: 10 } } });
    const out = await centralBankAPI.getBalance();
    expect(out).toEqual({ credits: 500, commodities: { fuel: 10 } });
    expect(get).toHaveBeenCalledWith('/api/v1/central-bank/balance', jsonHeaders);
  });

  it('centralBankAPI.withdrawCredits POSTs amount', async () => {
    post.mockResolvedValue({ data: { withdrawn: 50, bank_credits_remaining: 450, wallet_credits: 1050 } });
    const out = await centralBankAPI.withdrawCredits(50);
    expect(out).toEqual({ withdrawn: 50, bank_credits_remaining: 450, wallet_credits: 1050 });
    expect(post).toHaveBeenCalledWith(
      '/api/v1/central-bank/withdraw/credits',
      JSON.stringify({ amount: 50 }),
      jsonHeaders,
    );
  });

  it('centralBankAPI.withdrawCommodity POSTs commodity + quantity', async () => {
    post.mockResolvedValue({ data: { commodity: 'fuel', quantity: 20, turn_cost: 1, bank_commodities_remaining: {} } });
    const out = await centralBankAPI.withdrawCommodity('fuel', 20);
    expect(out).toEqual({ commodity: 'fuel', quantity: 20, turn_cost: 1, bank_commodities_remaining: {} });
    expect(post).toHaveBeenCalledWith(
      '/api/v1/central-bank/withdraw/commodity',
      JSON.stringify({ commodity: 'fuel', quantity: 20 }),
      jsonHeaders,
    );
  });

  it('citadelAPI.constructBuilding maps planet_minefield → PLANET_MINEFIELD', async () => {
    get.mockResolvedValue({ data: { plots: [{ x: 0, y: 0, cleared: true }] } });
    post.mockResolvedValue({ data: { success: true } });
    await citadelAPI.constructBuilding('p1', 'planet_minefield');
    expect(post).toHaveBeenCalledWith(
      '/api/v1/planets/p1/grid/place',
      JSON.stringify({ kind: 'PLANET_MINEFIELD', x: 0, y: 0, level: 1 }),
      jsonHeaders,
    );
  });

  it('planetaryAPI.withdrawStockpileToCargo POSTs tip path and payload', async () => {
    post.mockResolvedValue({
      data: { success: true, message: 'Withdrew 10 fuel ore to cargo.', amount_to_cargo: 10, tax_skimmed: 0 },
    });
    const out = await planetaryAPI.withdrawStockpileToCargo('planet-9', 'fuel_ore', 10);
    expect(out.success).toBe(true);
    expect(post).toHaveBeenCalledWith(
      '/api/v1/planets/planet-9/stockpile/withdraw',
      JSON.stringify({ commodity: 'fuel_ore', amount: 10 }),
      jsonHeaders,
    );
  });

  it('planetaryAPI.withdrawStockpileToCargo surfaces 403 non-owner detail', async () => {
    post.mockRejectedValue(
      axiosHttpError(403, {
        detail: 'You do not own this planet and are not on the owner\'s team',
      }),
    );
    await expect(
      planetaryAPI.withdrawStockpileToCargo('planet-9', 'organics', 1),
    ).rejects.toThrow('You do not own this planet and are not on the owner\'s team');
  });

  it('planetaryAPI.offerOwnershipTransfer POSTs recipient_player_id', async () => {
    post.mockResolvedValue({
      data: { success: true, planet_id: 'planet-1', offer: { fee_credits: 12 } },
    });
    const out = await planetaryAPI.offerOwnershipTransfer('planet-1', 'player-9');
    expect(out).toEqual({
      success: true,
      planet_id: 'planet-1',
      offer: { fee_credits: 12 },
    });
    expect(post).toHaveBeenCalledWith(
      '/api/v1/planets/planet-1/ownership-transfer',
      JSON.stringify({ recipient_player_id: 'player-9' }),
      jsonHeaders,
    );
  });

  it('planetaryAPI.acceptOwnershipTransfer POSTs /accept', async () => {
    post.mockResolvedValue({
      data: { success: true, planet_id: 'planet-1', fee_credits: 12 },
    });
    await planetaryAPI.acceptOwnershipTransfer('planet-1');
    expect(post).toHaveBeenCalledWith(
      '/api/v1/planets/planet-1/ownership-transfer/accept',
      undefined,
      jsonHeaders,
    );
  });

  it('planetaryAPI.getOwnershipTransfer GETs status', async () => {
    get.mockResolvedValue({ data: { planet_id: 'planet-1', pending: false, offer: null } });
    const out = await planetaryAPI.getOwnershipTransfer('planet-1');
    expect(out).toEqual({ planet_id: 'planet-1', pending: false, offer: null });
    expect(get).toHaveBeenCalledWith(
      '/api/v1/planets/planet-1/ownership-transfer',
      jsonHeaders,
    );
  });

  it('portOwnershipAPI.militaryTakeover POSTs action to /stations/{id}/military', async () => {
    post.mockResolvedValue({ data: { campaign_type: 'military', status: 'building' } });
    const out = await portOwnershipAPI.militaryTakeover('st-1', 'declare');
    expect(out).toEqual({ campaign_type: 'military', status: 'building' });
    expect(post).toHaveBeenCalledWith(
      '/api/v1/port-ownership/stations/st-1/military',
      JSON.stringify({ action: 'declare' }),
      jsonHeaders,
    );
  });

});
