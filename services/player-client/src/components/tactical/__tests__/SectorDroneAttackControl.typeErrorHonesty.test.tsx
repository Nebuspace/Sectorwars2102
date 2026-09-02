// @vitest-environment jsdom
/**
 * LEG-3992 Soft-ORDER — SectorDroneAttackControl 403/429 typeErrorHonesty (formatter-only invent=0).
 */
import { describe, expect, it } from 'vitest';
import { formatSectorDroneAttackError } from '../SectorDroneAttackControl';

const FALLBACK = 'Sector drone attack failed';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatSectorDroneAttackError typeErrorHonesty (LEG-3992)', () => {
  it('collapses TypeError network collapse to fallback without transport strings', () => {
    const text = formatSectorDroneAttackError(new TypeError('Failed to fetch'), FALLBACK);
    expect(text).toBe(FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatSectorDroneAttackError(new Error('Network Error'), FALLBACK)).toBe(FALLBACK);
    expect(formatSectorDroneAttackError(new Error('Failed to fetch'), FALLBACK)).toBe(FALLBACK);
    expect(formatSectorDroneAttackError(new Error('Network Error'), FALLBACK)).not.toMatch(
      /Network Error/i,
    );
  });

  it('surfaces 403/429 without raw status codes', () => {
    expect(formatSectorDroneAttackError(apiRequestError(403), FALLBACK)).toMatch(/permission/i);
    expect(formatSectorDroneAttackError(apiRequestError(403, 'attack_denied'), FALLBACK)).toBe(
      'attack_denied',
    );
    expect(formatSectorDroneAttackError(apiRequestError(429), FALLBACK)).toMatch(/rate limit/i);
    expect(formatSectorDroneAttackError(apiRequestError(429), FALLBACK)).not.toMatch(/\b429\b/);
    expect(formatSectorDroneAttackError(apiRequestError(403), FALLBACK)).not.toMatch(/TypeError/i);
    expect(formatSectorDroneAttackError(apiRequestError(403), FALLBACK)).not.toMatch(
      /Network Error/i,
    );
  });
});
