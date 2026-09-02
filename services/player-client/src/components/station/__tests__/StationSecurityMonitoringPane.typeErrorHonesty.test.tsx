// @vitest-environment jsdom
/**
 * LEG-3160 Soft-ORDER — StationSecurityMonitoringPane TypeError densify.
 */
import { describe, it, expect } from 'vitest';
import { formatStationSecurityError } from '../StationSecurityMonitoringPane';

describe('formatStationSecurityError (LEG-3160)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatStationSecurityError(
      new TypeError('Failed to fetch'),
      'Failed to load security tier',
    );
    expect(text).toBe('Failed to load security tier');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves axios detail for structured server errors', () => {
    const err = Object.assign(new Error('request failed'), {
      response: { data: { detail: 'Insufficient credits for security upgrade.' } },
    });
    expect(formatStationSecurityError(err, 'upgrade failed')).toBe(
      'Insufficient credits for security upgrade.',
    );
  });

  it('preserves Error.message when no response detail', () => {
    expect(formatStationSecurityError(new Error('Station not found'), 'downgrade failed')).toBe(
      'Station not found',
    );
  });

  it('falls back on axios Network Error / Failed to fetch non-TypeError (LEG-3289)', () => {
    const fallback = 'Failed to load security tier';
    expect(formatStationSecurityError(new Error('Network Error'), fallback)).toBe(fallback);
    expect(formatStationSecurityError(new Error('Failed to fetch'), fallback)).toBe(fallback);
    expect(formatStationSecurityError(new Error('   '), fallback)).toBe(fallback);
  });
});


describe('formatStationSecurityError 403/429 densify (LEG-4087)', () => {
  const apiRequestError = (status: number, message?: string) => {
    const err = new Error(message ?? `API Error: ${status}`);
    (err as { status?: number }).status = status;
    return err;
  };
  it('surfaces 403/429 without raw status codes', () => {
    const fallback = 'Failed to update security';
    expect(formatStationSecurityError(apiRequestError(403), fallback)).toMatch(/permission/i);
    expect(formatStationSecurityError(apiRequestError(403, 'security_denied'), fallback)).toBe(
      'security_denied',
    );
    expect(formatStationSecurityError(apiRequestError(429), fallback)).toMatch(/rate limit/i);
    expect(formatStationSecurityError(apiRequestError(429), fallback)).not.toMatch(/\b429\b/);
    expect(formatStationSecurityError(apiRequestError(403), fallback)).not.toMatch(/TypeError/i);
  });
});
