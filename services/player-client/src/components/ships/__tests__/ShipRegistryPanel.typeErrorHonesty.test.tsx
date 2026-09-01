// @vitest-environment jsdom
/**
 * LEG-3421 Soft-ORDER — ShipRegistryPanel Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatShipRegistryActionError } from '../ShipRegistryPanel';

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
