import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../utils/auth';
import {
  GOLD_BUBBLE_INTERIOR_SIZE_MIN,
  parseSectorIdList,
  placeGoldBubble,
} from './placeGoldBubbleApi';

vi.mock('../utils/auth', () => ({
  api: {
    post: vi.fn(),
  },
}));

describe('placeGoldBubbleApi (LEG-184)', () => {
  beforeEach(() => {
    vi.mocked(api.post).mockReset();
  });

  it('parseSectorIdList dedupes and splits commas/newlines', () => {
    const a = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
    const b = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
    expect(parseSectorIdList(`${a},\n${b} ${a}`)).toEqual([a, b]);
  });

  it('placeGoldBubble posts PlaceGoldBubbleRequest with no invented fields', async () => {
    const regionId = '11111111-1111-4111-8111-111111111111';
    const gateways = ['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'];
    const interiors = Array.from({ length: GOLD_BUBBLE_INTERIOR_SIZE_MIN }, (_, i) => {
      const n = (i + 1).toString(16).padStart(12, '0');
      return `cccccccc-cccc-4ccc-8ccc-${n}`;
    });
    const body = {
      gateway_sector_ids: gateways,
      interior_sector_ids: interiors,
      name: 'Operator Bubble',
      isolate_warps: true,
    };
    const formation = {
      id: 'ffffffff-ffff-4fff-8fff-ffffffffffff',
      type: 'GOLD_BUBBLE',
      name: 'Operator Bubble',
      region_id: regionId,
      anchor_sector_id: gateways[0],
      interior_sector_ids: interiors,
      properties: { gateway_count: 1 },
      discovery_requirement: null,
    };
    vi.mocked(api.post).mockResolvedValue({
      data: { success: true, formation },
    });

    await expect(placeGoldBubble(regionId, body)).resolves.toEqual({
      success: true,
      formation,
    });
    expect(api.post).toHaveBeenCalledWith(
      `/api/v1/admin/regions/${regionId}/formations/gold-bubble`,
      body,
    );
  });
});
