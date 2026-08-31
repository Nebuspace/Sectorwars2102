// @vitest-environment jsdom
/**
 * LEG-3489 Soft-ORDER — RegionOwnerControls Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatRegionOwnerProbeError } from '../RegionOwnerControls';

describe('RegionOwnerControls TypeError densify (LEG-3489)', () => {
  it('formatRegionOwnerProbeError falls back on TypeError network collapse', () => {
    const text = formatRegionOwnerProbeError(new TypeError('Failed to fetch'));
    expect(text).toBe('Region status unavailable — try again shortly.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatRegionOwnerProbeError(new Error('Network Error'))).toBe(
      'Region status unavailable — try again shortly.',
    );
    expect(formatRegionOwnerProbeError(new Error('Failed to fetch'))).toBe(
      'Region status unavailable — try again shortly.',
    );
    expect(formatRegionOwnerProbeError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('returns null for expected not-owner found heuristic', () => {
    expect(formatRegionOwnerProbeError(new Error('Region not found'))).toBeNull();
  });
});
