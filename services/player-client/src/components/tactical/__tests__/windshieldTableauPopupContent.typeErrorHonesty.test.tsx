// @vitest-environment jsdom
/**
 * LEG-3081 Soft-ORDER — windshieldTableauPopupContent TypeError densify.
 */
import { describe, it, expect } from 'vitest';
import { formatBeaconPopupError } from '../windshieldTableauPopupContent';

describe('windshieldTableauPopupContent TypeError densify (LEG-3081)', () => {
  it('formatBeaconPopupError falls back on TypeError network collapse', () => {
    const text = formatBeaconPopupError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Action failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatBeaconPopupError(new Error('beacon_denied'))).toBe('beacon_denied');
  });

  it('formatBeaconPopupError falls back on axios Network Error / Failed to fetch (LEG-3363)', () => {
    expect(formatBeaconPopupError(new Error('Network Error'))).toBe('Action failed');
    expect(formatBeaconPopupError(new Error('Failed to fetch'))).toBe('Action failed');
    expect(formatBeaconPopupError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });
});


describe('formatBeaconPopupError 403/429 densify (LEG-4095)', () => {
  const apiRequestError = (status: number, message?: string) => {
    const err = new Error(message ?? `API Error: ${status}`);
    (err as { status?: number }).status = status;
    return err;
  };
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatBeaconPopupError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatBeaconPopupError(apiRequestError(403, 'beacon_denied'))).toBe('beacon_denied');
    expect(formatBeaconPopupError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatBeaconPopupError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatBeaconPopupError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
