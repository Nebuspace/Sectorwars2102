/**
 * api.ts apiRequest error shaping + money/combat/trade wrappers
 * (WO-TESTCOV-PLAYER-API-CLIENT).
 */
import { AxiosError } from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../apiClient', () => ({
  default: {
    request: vi.fn(),
  },
}));

import apiClient from '../apiClient';
import { combatAPI, greyStatusAPI, shipRegistryAPI, tradeAPI } from '../api';

const request = apiClient.request as ReturnType<typeof vi.fn>;

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

describe('apiRequest via trade/combat/grey wrappers', () => {
  beforeEach(() => {
    request.mockReset();
  });

  it('tradeAPI.initiate POSTs target_player_id and returns body', async () => {
    request.mockResolvedValue({ data: { id: 'sess-1' } });
    const out = await tradeAPI.initiate('player-9');
    expect(out).toEqual({ id: 'sess-1' });
    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({
        url: '/api/v1/trade/initiate',
        method: 'POST',
        data: JSON.stringify({ target_player_id: 'player-9' }),
      }),
    );
  });

  it('tradeAPI.offer defaults empty offer fields', async () => {
    request.mockResolvedValue({ data: { ok: true } });
    await tradeAPI.offer('s1', { credits: 50 });
    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({
        url: '/api/v1/trade/s1/offer',
        method: 'POST',
        data: JSON.stringify({
          credits: 50,
          commodities: {},
          ship_id: null,
          ships: [],
        }),
      }),
    );
  });

  it('combatAPI.engage posts targetType/targetId', async () => {
    request.mockResolvedValue({ data: { combatId: 'c1' } });
    await expect(combatAPI.engage('port', 'port-1')).resolves.toEqual({
      combatId: 'c1',
    });
    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({
        url: '/api/v1/combat/engage',
        method: 'POST',
        data: JSON.stringify({ targetType: 'port', targetId: 'port-1' }),
      }),
    );
  });

  it('surfaces string detail from FastAPI errors', async () => {
    request.mockRejectedValue(axiosHttpError(400, { detail: 'not enough credits' }));
    await expect(greyStatusAPI.clearFine()).rejects.toThrow('not enough credits');
  });

  it('surfaces message when detail is absent', async () => {
    request.mockRejectedValue(axiosHttpError(500, { message: 'backend blew up' }));
    await expect(tradeAPI.get('x')).rejects.toThrow('backend blew up');
  });

  it('attaches structured detail.errors/code/regions on thrown Error', async () => {
    request.mockRejectedValue(
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
    request.mockRejectedValue(axiosHttpError(503, {}));
    await expect(combatAPI.getStatus('c9')).rejects.toThrow('API Error: 503');
  });

  it('rethrows non-response axios failures unchanged', async () => {
    const net = new AxiosError('Network Error');
    request.mockRejectedValue(net);
    await expect(tradeAPI.getOpen()).rejects.toBe(net);
  });

  it('rethrows non-axios errors unchanged', async () => {
    const boom = new Error('adapter exploded');
    request.mockRejectedValue(boom);
    await expect(tradeAPI.accept('s')).rejects.toBe(boom);
  });

  it('shipRegistryAPI.eject POSTs with no body and no ship_id', async () => {
    request.mockResolvedValue({ data: { ejected_ship_id: 'ship-1', turns_spent: 1 } });
    const out = await shipRegistryAPI.eject();
    expect(out).toEqual({ ejected_ship_id: 'ship-1', turns_spent: 1 });
    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({
        url: '/api/v1/players/me/eject',
        method: 'POST',
        data: undefined,
      }),
    );
  });

  it('shipRegistryAPI.board omits pin from the body when not supplied', async () => {
    request.mockResolvedValue({ data: { boarded: true, state: 'owner_aboard' } });
    await shipRegistryAPI.board('ship-2');
    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({
        url: '/api/v1/ships/ship-2/board',
        method: 'POST',
        data: JSON.stringify({}),
      }),
    );
  });

  it('shipRegistryAPI.board includes the pin when supplied', async () => {
    request.mockResolvedValue({ data: { boarded: true, state: 'borrowed' } });
    await shipRegistryAPI.board('ship-2', 'ABC123');
    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({
        url: '/api/v1/ships/ship-2/board',
        method: 'POST',
        data: JSON.stringify({ pin: 'ABC123' }),
      }),
    );
  });
});
