// @vitest-environment jsdom
/**
 * LEG-4075 Soft-ORDER — SafeVaultPanel 403/429 densify (invent=0).
 */
import { describe, expect, it } from 'vitest';
import { SAFE_VAULT_FALLBACK, formatSafeVaultError } from '../SafeVaultPanel';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatSafeVaultError 403/429 densify (LEG-4075)', () => {
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatSafeVaultError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatSafeVaultError(apiRequestError(403, 'vault_denied'))).toBe('vault_denied');
    expect(formatSafeVaultError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatSafeVaultError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatSafeVaultError(apiRequestError(403))).not.toMatch(/API Error/i);
  });

  it('falls back on TypeError / Network Error', () => {
    expect(formatSafeVaultError(new TypeError('Failed to fetch'))).toBe(SAFE_VAULT_FALLBACK);
    expect(formatSafeVaultError(new Error('Network Error'))).toBe(SAFE_VAULT_FALLBACK);
  });
});
