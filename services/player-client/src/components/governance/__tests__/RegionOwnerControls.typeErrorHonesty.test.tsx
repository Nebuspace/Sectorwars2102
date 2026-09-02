// @vitest-environment jsdom
/**
 * LEG-3489 Soft-ORDER — RegionOwnerControls Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatRegionOwnerProbeError } from '../RegionOwnerControls';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

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

describe('RegionOwnerControls 403/429 densify (LEG-3997)', () => {
  it('formatRegionOwnerProbeError surfaces 403/429 without raw status codes', () => {
    expect(formatRegionOwnerProbeError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatRegionOwnerProbeError(apiRequestError(403, 'region_probe_denied'))).toBe(
      'region_probe_denied',
    );
    expect(formatRegionOwnerProbeError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatRegionOwnerProbeError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatRegionOwnerProbeError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatRegionOwnerProbeError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });
});
