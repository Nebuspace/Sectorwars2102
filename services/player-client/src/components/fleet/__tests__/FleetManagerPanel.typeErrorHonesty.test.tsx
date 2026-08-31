// @vitest-environment jsdom
/**
 * LEG-3414 Soft-ORDER — FleetManagerPanel Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatFleetManagerError } from '../FleetManagerPanel';

describe('FleetManagerPanel TypeError densify (LEG-3414)', () => {
  it('formatFleetManagerError falls back on TypeError network collapse', () => {
    const text = formatFleetManagerError(new TypeError('Failed to fetch'));
    expect(text).toBe('Fleet request failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatFleetManagerError(new Error('Network Error'))).toBe('Fleet request failed');
    expect(formatFleetManagerError(new Error('Failed to fetch'))).toBe('Fleet request failed');
    expect(formatFleetManagerError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatFleetManagerError(new Error('fleet_busy'))).toBe('fleet_busy');
  });
});
