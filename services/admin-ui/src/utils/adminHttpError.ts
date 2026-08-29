/**
 * Inline colonization helper for invent=0 403/429 honesty when
 * formatAdminApiError is tip-absent (Soft-HOLD #760). Does not parse
 * server scope bodies — callers pass the known required scope label.
 */

export function adminHttpStatus(error: unknown): number | undefined {
  if (error && typeof error === 'object' && 'response' in error) {
    const status = (error as { response?: { status?: number } }).response?.status;
    if (typeof status === 'number') return status;
  }
  return undefined;
}

export function adminHttpErrorMessage(
  error: unknown,
  fallback: string,
  deniedScope?: string,
): string {
  const status = adminHttpStatus(error);
  if (status === 401 || status === 403) {
    return deniedScope
      ? `Access denied — requires the ${deniedScope} scope.`
      : 'Access denied — insufficient admin scope.';
  }
  if (status === 429) {
    return 'Admin rate limit exceeded — wait a moment and try again.';
  }
  return fallback;
}
