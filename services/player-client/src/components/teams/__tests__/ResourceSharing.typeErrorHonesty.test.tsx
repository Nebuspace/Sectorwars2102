// @vitest-environment jsdom
/**
 * LEG-3079 Soft-ORDER — ResourceSharing TypeError densify.
 * LEG-3548 Soft-ORDER — Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatTreasuryOpError } from '../ResourceSharing';

describe('ResourceSharing TypeError densify (LEG-3079)', () => {
  it('formatTreasuryOpError falls back on TypeError network collapse', () => {
    const text = formatTreasuryOpError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Operation failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch (LEG-3548)', () => {
    expect(formatTreasuryOpError(new Error('Network Error'))).toBe('Operation failed.');
    expect(formatTreasuryOpError(new Error('Failed to fetch'))).toBe('Operation failed.');
    expect(formatTreasuryOpError(new Error('Network Error'))).not.toMatch(/Network Error/i);
    expect(formatTreasuryOpError(new Error('Failed to fetch'))).not.toMatch(/Failed to fetch/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatTreasuryOpError(new Error('treasury_denied'))).toBe('treasury_denied');
  });
});


describe('formatTreasuryOpError 403/429 densify (LEG-4092)', () => {
  const apiRequestError = (status: number, message?: string) => {
    const err = new Error(message ?? `API Error: ${status}`);
    (err as { status?: number }).status = status;
    return err;
  };
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatTreasuryOpError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatTreasuryOpError(apiRequestError(403, 'treasury_denied'))).toBe('treasury_denied');
    expect(formatTreasuryOpError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatTreasuryOpError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatTreasuryOpError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
