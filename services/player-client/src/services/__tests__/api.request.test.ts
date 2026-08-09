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
import { combatAPI, greyStatusAPI, miningAPI, navAPI, playerAPI, shipRegistryAPI, tradeAPI } from '../api';

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
});
