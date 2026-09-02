// @vitest-environment jsdom
/**
 * LEG-3074 Soft-ORDER — SiegeStatusMonitor TypeError densify.
 * Aid/hail must not surface raw Failed to fetch / TypeError.
 */
import { describe, it, expect } from 'vitest';
import {
  formatSiegeAidError,
  formatSiegeHailError,
} from '../SiegeStatusMonitor';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('SiegeStatusMonitor TypeError densify (LEG-3074)', () => {
  it('formatSiegeAidError falls back on TypeError network collapse', () => {
    const text = formatSiegeAidError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to send emergency aid request.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatSiegeHailError falls back on TypeError network collapse', () => {
    const text = formatSiegeHailError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to send negotiation hail.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatSiegeAidError(new Error('siege_aid_denied'))).toBe('siege_aid_denied');
    expect(formatSiegeHailError(new Error('siege_hail_denied'))).toBe('siege_hail_denied');
  });

  it('formatSiegeAid/Hail fall back on axios Network Error / Failed to fetch (LEG-3366)', () => {
    expect(formatSiegeAidError(new Error('Network Error'))).toBe('Failed to send emergency aid request.');
    expect(formatSiegeAidError(new Error('Failed to fetch'))).toBe('Failed to send emergency aid request.');
    expect(formatSiegeHailError(new Error('Network Error'))).toBe('Failed to send negotiation hail.');
    expect(formatSiegeHailError(new Error('Failed to fetch'))).toBe('Failed to send negotiation hail.');
    expect(formatSiegeAidError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });
});

describe('SiegeStatusMonitor 403/429 densify (LEG-3979)', () => {
  it('formatSiegeAidError surfaces 403/429 without raw status codes', () => {
    expect(formatSiegeAidError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatSiegeAidError(apiRequestError(403, 'siege_aid_denied'))).toBe('siege_aid_denied');
    expect(formatSiegeAidError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatSiegeAidError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatSiegeAidError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatSiegeAidError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });

  it('formatSiegeHailError surfaces 403/429 without raw status codes', () => {
    expect(formatSiegeHailError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatSiegeHailError(apiRequestError(403, 'siege_hail_denied'))).toBe('siege_hail_denied');
    expect(formatSiegeHailError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatSiegeHailError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatSiegeHailError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatSiegeHailError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });
});
