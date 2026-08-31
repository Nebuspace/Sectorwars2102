// @vitest-environment jsdom
/**
 * LEG-3471 Soft-ORDER — ShipSelector Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatShipSelectorError } from '../ShipSelector';

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
