// @vitest-environment jsdom
/**
 * LEG-3471 Soft-ORDER — ShipSelector Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatShipSelectorError } from '../ShipSelector';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('ShipSelector TypeError densify (LEG-3471)', () => {
  it('formatShipSelectorError falls back on TypeError network collapse', () => {
    const text = formatShipSelectorError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to change ship. Please try again.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatShipSelectorError(new Error('Network Error'))).toBe(
      'Failed to change ship. Please try again.',
    );
    expect(formatShipSelectorError(new Error('Failed to fetch'))).toBe(
      'Failed to change ship. Please try again.',
    );
    expect(formatShipSelectorError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatShipSelectorError(new Error('ship_busy'))).toBe('ship_busy');
  });
});

describe('ShipSelector 403/429 densify (LEG-4002)', () => {
  it('formatShipSelectorError surfaces 403/429 without raw status codes', () => {
    expect(formatShipSelectorError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatShipSelectorError(apiRequestError(403, 'ship_change_denied'))).toBe(
      'ship_change_denied',
    );
    expect(formatShipSelectorError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatShipSelectorError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatShipSelectorError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatShipSelectorError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });
});
