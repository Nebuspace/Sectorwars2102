/**
 * Thin axios wrappers around the five sw2102-bang admin endpoints.
 *
 *   POST   /api/v1/admin/galaxy/jobs           — start a generation job
 *   POST   /api/v1/admin/galaxy/preview        — preview / validate only
 *   GET    /api/v1/admin/galaxy/jobs/{id}      — job detail
 *   GET    /api/v1/admin/galaxy/jobs?page=...  — history listing (planned)
 *   DELETE /api/v1/admin/galaxy/{galaxy_id}    — hard-delete (typed-name)
 *
 * SSE log stream is *not* here — see `hooks/useBangGenerationStream.ts`
 * (browsers can't set Authorization on EventSource so it uses `?token=`).
 *
 * All callers pass the bearer token explicitly to match the per-call
 * header pattern established in `AdminContext`. The shared response
 * interceptor in `AuthContext` will handle 401 refresh transparently.
 */
import axios from 'axios';

import type {
  BangConfig,
  BangJobCreate,
  BangJobHistoryPage,
  BangJobResponse,
  BangPreviewResponse,
} from '../components/universe/bang/types';

const api = axios.create({ baseURL: '/api/v1' });

function authHeaders(token: string | null): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** POST /admin/galaxy/jobs — returns 202 + the created job row. */
export async function createBangJob(
  payload: BangJobCreate,
  token: string | null,
): Promise<BangJobResponse> {
  const response = await api.post<BangJobResponse>(
    '/admin/galaxy/jobs',
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
    `/admin/galaxy/${galaxyId}/regions`,
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
    '/admin/galaxy/preview',
    config,
    { headers: authHeaders(token) },
  );
  return response.data;
}

/** GET /admin/galaxy/jobs/{id} — full job record. */
export async function getBangJob(
  jobId: string,
  token: string | null,
): Promise<BangJobResponse> {
  const response = await api.get<BangJobResponse>(
    `/admin/galaxy/jobs/${jobId}`,
    { headers: authHeaders(token) },
  );
  return response.data;
}

/**
 * GET /admin/galaxy/jobs?page=&page_size= — paginated history.
 *
 * NOTE: as of Phase 3 the backend list endpoint is planned but not yet
 * implemented (see DOCS/PLANS/bang-integration.md § Phase 1D). The shape
 * below is what Phase 3 expects; the History component degrades to an
 * empty list if the endpoint 404s.
 */
export async function listBangJobs(
  page: number,
  pageSize: number,
  token: string | null,
): Promise<BangJobHistoryPage> {
  const response = await api.get<BangJobHistoryPage>('/admin/galaxy/jobs', {
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
  await api.delete(`/admin/galaxy/${galaxyId}`, {
    headers: {
      ...authHeaders(token),
      'X-Confirm-Galaxy-Name': confirmName,
    },
  });
}
