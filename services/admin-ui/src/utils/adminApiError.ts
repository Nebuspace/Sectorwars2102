/**
 * Shared admin API error formatting — canon: sw2102-docs OPERATIONS/admin-ui.md § RBAC.
 * 403 → scope-aware copy; 429 → rate-limit copy; never masquerade scope denial as "unimplemented".
 */

export function axiosResponseStatus(err: unknown): number | undefined {
  if (typeof err !== 'object' || err === null || !('response' in err)) {
    return undefined;
  }
  return (err as { response?: { status?: number } }).response?.status;
}

export function detailFromResponse(err: unknown): string | undefined {
  if (typeof err !== 'object' || err === null || !('response' in err)) {
    return undefined;
  }
  const data = (err as { response?: { data?: { detail?: unknown; message?: unknown } } })
    .response?.data;
  if (typeof data?.detail === 'string' && data.detail.trim()) {
    return data.detail;
  }
  if (Array.isArray(data?.detail)) {
    const parts = data.detail
      .map((item: unknown) => {
        if (typeof item === 'object' && item !== null && 'msg' in item) {
          const msg = (item as { msg?: unknown }).msg;
          return typeof msg === 'string' ? msg : JSON.stringify(item);
        }
        return JSON.stringify(item);
      })
      .filter(Boolean);
    if (parts.length) {
      return parts.join('; ');
    }
  }
  if (typeof data?.message === 'string' && data.message.trim()) {
    return data.message;
  }
  return undefined;
}

export interface AdminApiErrorOptions {
  fallback: string;
  /** Shown when 403 has no string detail in the response body. */
  scopeHint?: string;
  notFoundMessage?: string;
  rateLimitMessage?: string;
}

const DEFAULT_RATE_LIMIT =
  'Admin rate limit exceeded — wait a moment and try again.';

export function formatAdminApiError(err: unknown, options: AdminApiErrorOptions): string {
  const status = axiosResponseStatus(err);
  const detail = detailFromResponse(err);

  if (status === 403) {
    if (detail) {
      return detail;
    }
    if (options.scopeHint) {
      return `Access denied — ${options.scopeHint}`;
    }
    return 'Access denied — you lack the required admin scope for this action.';
  }

  if (status === 429) {
    return options.rateLimitMessage ?? DEFAULT_RATE_LIMIT;
  }

  if (status === 404) {
    return (
      options.notFoundMessage ??
      'Resource not found (404). Verify the gameserver route and API proxy.'
    );
  }

  if (status !== undefined) {
    return detail ?? `${options.fallback} (HTTP ${status})`;
  }

  return options.fallback;
}

/** Label a rejected Promise.allSettled entry with scope/rate-limit aware copy. */
export function formatSettledRejection(
  reason: unknown,
  label: string,
  scopeHint?: string
): string {
  return `${label}: ${formatAdminApiError(reason, {
    fallback: 'unavailable',
    scopeHint,
  })}`;
}
