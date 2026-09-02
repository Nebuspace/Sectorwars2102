// @vitest-environment jsdom
/**
 * LEG-3469 Soft-ORDER — SalvageBreakCta Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatSalvageBreakError } from '../SalvageBreakCta';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('SalvageBreakCta TypeError densify (LEG-3469)', () => {
  it('formatSalvageBreakError falls back on TypeError network collapse', () => {
    const text = formatSalvageBreakError(new TypeError('Failed to fetch'));
    expect(text).toBe('Salvage break failed — check your connection and try again.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatSalvageBreakError(new Error('Network Error'))).toBe(
      'Salvage break failed — check your connection and try again.',
    );
    expect(formatSalvageBreakError(new Error('Failed to fetch'))).toBe(
      'Salvage break failed — check your connection and try again.',
    );
    expect(formatSalvageBreakError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatSalvageBreakError(new Error('hull_locked'))).toBe('hull_locked');
  });
});

describe('SalvageBreakCta 403/429 densify (LEG-3982)', () => {
  it('formatSalvageBreakError surfaces 403/429 without raw status codes', () => {
    expect(formatSalvageBreakError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatSalvageBreakError(apiRequestError(403, 'salvage_denied'))).toBe('salvage_denied');
    expect(formatSalvageBreakError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatSalvageBreakError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatSalvageBreakError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatSalvageBreakError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });
});
