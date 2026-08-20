/**
 * Thin wrappers around the sw2102-bang admin endpoints via shared `api`
 * (`utils/auth.ts` — JWT interceptor + empty baseURL / Vite proxy).
 *
 *   POST   /api/v1/admin/galaxy/jobs           — start a generation job
 *   POST   /api/v1/admin/galaxy/preview        — preview / validate only
 *   GET    /api/v1/admin/galaxy/jobs?page=...  — history listing (BangJobListItem)
 *   DELETE /api/v1/admin/galaxy/{galaxy_id}    — hard-delete (typed-name)
 *
 * SSE log stream is *not* here — see `hooks/useBangGenerationStream.ts`
 * (browsers can't set Authorization on EventSource so it uses `?token=`).
 *
 * Callers still pass a bearer token so request headers stay explicit
 * (same overlay as the old per-instance pattern). The shared interceptor
 * also attaches `accessToken` from localStorage when present.
 */
import { api } from '../utils/auth';

import type {
  BangConfig,
  BangJobCreate,
  BangJobHistoryPage,
  BangJobResponse,
  BangPreviewResponse,
} from '../components/universe/bang/types';

function authHeaders(token: string | null): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** POST /admin/galaxy/jobs — returns 202 + the created job row. */
export async function createBangJob(
  payload: BangJobCreate,
  token: string | null,
): Promise<BangJobResponse> {
  const response = await api.post<BangJobResponse>(
    '/api/v1/admin/galaxy/jobs',
    payload,
    { headers: authHeaders(token) },
  );
  return response.data;
}

/**
 * POST /admin/galaxy/{galaxy_id}/regions — additive: grow an existing
 * galaxy by ONE player_owned region. Backend forces region_type to
 * player_owned and clamps sectors to [100, 1000]. Returns the job row
 * (202); subscribe to the SSE log stream the same way as the full-
 * generation flow.
 */
export async function addPlayerOwnedRegion(
  galaxyId: string,
  payload: BangJobCreate,
  token: string | null,
): Promise<BangJobResponse> {
  const response = await api.post<BangJobResponse>(
    `/api/v1/admin/galaxy/${galaxyId}/regions`,
    payload,
    { headers: authHeaders(token) },
  );
  return response.data;
}

/** POST /admin/galaxy/preview — runs bang with --validate-only inline. */
export async function previewBangConfig(
  config: BangConfig,
  token: string | null,
): Promise<BangPreviewResponse> {
  const response = await api.post<BangPreviewResponse>(
    '/api/v1/admin/galaxy/preview',
    config,
    { headers: authHeaders(token) },
  );
  return response.data;
}

/**
 * GET /admin/galaxy/jobs?page=&page_size= — paginated history.
 *
 * Returns slim BangJobListItem rows (warning_count; no log_text /
 * warnings_json). Detail + SSE remain on GET /jobs/{id}.
 */
export async function listBangJobs(
  page: number,
  pageSize: number,
  token: string | null,
): Promise<BangJobHistoryPage> {
  const response = await api.get<BangJobHistoryPage>('/api/v1/admin/galaxy/jobs', {
    params: { page, page_size: pageSize },
    headers: authHeaders(token),
  });
  return response.data;
}

/**
 * DELETE /admin/galaxy/{galaxy_id} — hard-delete; cascade.
 *
 * The backend requires the `X-Confirm-Galaxy-Name` header to exactly
 * match the galaxy's name. The dialog enforces this client-side too,
 * but the backend is the authoritative gate.
 */
export async function wipeBangGalaxy(
  galaxyId: string,
  confirmName: string,
  token: string | null,
): Promise<void> {
  await api.delete(`/api/v1/admin/galaxy/${galaxyId}`, {
    headers: {
      ...authHeaders(token),
      'X-Confirm-Galaxy-Name': confirmName,
    },
  });
}
