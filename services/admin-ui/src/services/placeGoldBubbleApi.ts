/**
 * Operator Gold Bubble placement (LEG-184) — consumes LEG-52 contract.
 * GS route: POST /api/v1/admin/regions/{region_id}/formations/gold-bubble
 * Scope: admin.galaxy.manage (GALAXY_MANAGE). No invented request fields.
 */
import { api } from '../utils/auth';

/** Mirrors gameserver GOLD_BUBBLE_INTERIOR_SIZE_MIN (special_formation_service). */
export const GOLD_BUBBLE_INTERIOR_SIZE_MIN = 100;
export const GOLD_BUBBLE_GATEWAY_COUNT_MIN = 1;
export const GOLD_BUBBLE_GATEWAY_COUNT_MAX = 3;

export interface PlaceGoldBubbleRequest {
  gateway_sector_ids: string[];
  interior_sector_ids: string[];
  name?: string | null;
  discovery_requirement?: Record<string, unknown> | null;
  isolate_warps?: boolean;
}

export interface PlaceGoldBubbleFormation {
  id: string;
  type: string;
  name: string | null;
  region_id: string;
  anchor_sector_id: string;
  interior_sector_ids: string[];
  properties: Record<string, unknown>;
  discovery_requirement: Record<string, unknown> | null;
}

export interface PlaceGoldBubbleResponse {
  success: boolean;
  formation: PlaceGoldBubbleFormation;
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/** Split operator paste (comma / whitespace / newline) into unique UUID strings. */
export function parseSectorIdList(raw: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const token of raw.split(/[\s,;]+/)) {
    const id = token.trim();
    if (!id) continue;
    const key = id.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(id);
  }
  return out;
}

export function assertValidUuidList(ids: string[], label: string): void {
  const bad = ids.filter((id) => !UUID_RE.test(id));
  if (bad.length) {
    throw new Error(
      `${label}: ${bad.length} value(s) are not UUIDs (e.g. ${bad[0]}).`,
    );
  }
}

export async function placeGoldBubble(
  regionId: string,
  body: PlaceGoldBubbleRequest,
): Promise<PlaceGoldBubbleResponse> {
  const { data } = await api.post<PlaceGoldBubbleResponse>(
    `/api/v1/admin/regions/${regionId}/formations/gold-bubble`,
    body,
  );
  return data;
}
