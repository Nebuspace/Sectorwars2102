// @vitest-environment jsdom
/**
 * LEG-3421 Soft-ORDER — ShipRegistryPanel Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatShipRegistryActionError } from '../ShipRegistryPanel';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('ShipRegistryPanel TypeError densify (LEG-3421)', () => {
  it('formatShipRegistryActionError falls back on TypeError network collapse', () => {
    const text = formatShipRegistryActionError(new TypeError('Failed to fetch'));
    expect(text).toBe('Registry action failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatShipRegistryActionError(new Error('Network Error'))).toBe('Registry action failed');
    expect(formatShipRegistryActionError(new Error('Failed to fetch'))).toBe('Registry action failed');
    expect(formatShipRegistryActionError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatShipRegistryActionError(new Error('ship not registered'))).toBe(
      'ship not registered',
    );
  });
});

describe('ShipRegistryPanel 403/429 densify (LEG-3984)', () => {
  it('formatShipRegistryActionError surfaces 403/429 without raw status codes', () => {
    expect(formatShipRegistryActionError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatShipRegistryActionError(apiRequestError(403, 'registry_denied'))).toBe(
      'registry_denied',
    );
    expect(formatShipRegistryActionError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatShipRegistryActionError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatShipRegistryActionError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatShipRegistryActionError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });
});
