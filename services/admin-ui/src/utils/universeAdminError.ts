/**
 * Invent=0 colonization helper for universe child surfaces until
 * `formatAdminApiError` is tip-ancestor (#1113 / PR #760).
 * Distinguishes RBAC (403) and rate-limit (429) from bare Failed copy.
 */
export function formatUniverseAdminError(err: unknown, fallback: string): string {
  const e = err as {
    response?: { status?: number; data?: { detail?: unknown } };
    message?: string;
  };
  const status = e?.response?.status;
  if (status === 401 || status === 403) {
    return 'Access denied — this action requires the admin universe manage scope (admin.universe.manage).';
  }
  if (status === 429) {
    return 'Admin rate limit exceeded — wait a moment and try again.';
  }
  const detail = e?.response?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  // Transport collapse (TypeError / network) has no HTTP status — use fallback
  // (mirrors formatAdminApiError), never leak raw Failed to fetch / TypeError.
  if (status === undefined) return fallback;
  if (e?.message && e.message !== `HTTP ${status}`) return e.message;
  return fallback;
}
