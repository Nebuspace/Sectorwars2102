// @vitest-environment jsdom
/**
 * LEG-4101 Soft-ORDER — formatNavThreatError 403/429 densify.
 */
import { describe, expect, it } from 'vitest';
import { formatNavThreatError } from '../navThreat';

const FALLBACK = 'Threat data unavailable — check your connection.';

describe('formatNavThreatError TypeError densify', () => {
  it('collapses TypeError/network to fallback', () => {
    expect(formatNavThreatError(new TypeError('Failed to fetch'))).toBe(FALLBACK);
    expect(formatNavThreatError(new Error('Network Error'))).toBe(FALLBACK);
    expect(formatNavThreatError(new Error('Failed to fetch'))).toBe(FALLBACK);
  });

  it('preserves non-transport server detail', () => {
    expect(formatNavThreatError(new Error('sector_locked'))).toBe('sector_locked');
  });
});

describe('formatNavThreatError 403/429 densify (LEG-4101)', () => {
  const apiRequestError = (status: number, message?: string) => {
    const err = new Error(message ?? `API Error: ${status}`);
    (err as { status?: number }).status = status;
    return err;
  };

  it('surfaces 403/429 without raw status codes', () => {
    expect(formatNavThreatError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatNavThreatError(apiRequestError(403, 'threat_denied'))).toBe('threat_denied');
    expect(formatNavThreatError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatNavThreatError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatNavThreatError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
