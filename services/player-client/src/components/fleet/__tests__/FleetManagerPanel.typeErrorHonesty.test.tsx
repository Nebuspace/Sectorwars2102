// @vitest-environment jsdom
/**
 * LEG-3092 Soft-ORDER — FleetManagerPanel TypeError densify.
 */
import { describe, it, expect } from 'vitest';
import { formatFleetManagerError } from '../FleetManagerPanel';

describe('FleetManagerPanel TypeError densify (LEG-3092)', () => {
  it('formatFleetManagerError falls back on TypeError network collapse', () => {
    const text = formatFleetManagerError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Fleet request failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatFleetManagerError(new Error('fleet_not_found'))).toBe('fleet_not_found');
  });
});
