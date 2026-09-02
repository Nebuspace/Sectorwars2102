// @vitest-environment jsdom
/**
 * LEG-3737 Soft-ORDER — TacticalTargetPage hail/engage TypeError densify.
 */
import { describe, expect, it } from 'vitest';
import {
  formatTacticalTargetEngageError,
  formatTacticalTargetHailError,
} from '../TacticalTargetPage';

const HAIL_FALLBACK = 'TRANSMISSION FAILED';
const ENGAGE_FALLBACK = 'Combat system error — try again.';

describe('TacticalTargetPage TypeError densify (LEG-3737)', () => {
  it('formatTacticalTargetHailError falls back on TypeError network collapse', () => {
    const text = formatTacticalTargetHailError(new TypeError('Failed to fetch'));
    expect(text).toBe(HAIL_FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatTacticalTargetEngageError falls back on TypeError network collapse', () => {
    const text = formatTacticalTargetEngageError(new TypeError('Failed to fetch'));
    expect(text).toBe(ENGAGE_FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('hail/engage fall back on axios Network Error / Failed to fetch', () => {
    expect(formatTacticalTargetHailError(new Error('Network Error'))).toBe(HAIL_FALLBACK);
    expect(formatTacticalTargetHailError(new Error('Failed to fetch'))).toBe(HAIL_FALLBACK);
    expect(formatTacticalTargetHailError(new Error('Network Error'))).not.toMatch(/Network Error/i);

    expect(formatTacticalTargetEngageError(new Error('Network Error'))).toBe(ENGAGE_FALLBACK);
    expect(formatTacticalTargetEngageError(new Error('Failed to fetch'))).toBe(ENGAGE_FALLBACK);
    expect(formatTacticalTargetEngageError(new Error('hostile lock'))).toBe('hostile lock');
  });
});


describe('TacticalTargetPage 403/429 densify (LEG-4088)', () => {
  const apiRequestError = (status: number, message?: string) => {
    const err = new Error(message ?? `API Error: ${status}`);
    (err as { status?: number }).status = status;
    return err;
  };
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatTacticalTargetHailError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatTacticalTargetHailError(apiRequestError(403, 'hail_denied'))).toBe('hail_denied');
    expect(formatTacticalTargetHailError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatTacticalTargetHailError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatTacticalTargetEngageError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatTacticalTargetEngageError(apiRequestError(403, 'engage_denied'))).toBe(
      'engage_denied',
    );
    expect(formatTacticalTargetEngageError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatTacticalTargetEngageError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
