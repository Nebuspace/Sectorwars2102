// @vitest-environment jsdom
/**
 * LEG-4070 Soft-ORDER — GenesisVenue 403/429 densify (invent=0).
 */
import { describe, expect, it } from 'vitest';
import { GENESIS_VENUE_FALLBACK, formatGenesisVenueError } from '../GenesisVenue';

const FALLBACK = GENESIS_VENUE_FALLBACK;
const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatGenesisVenueError 403/429 densify (LEG-4070)', () => {
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatGenesisVenueError(apiRequestError(403), FALLBACK)).toMatch(/permission/i);
    expect(formatGenesisVenueError(apiRequestError(403, 'genesis_denied'), FALLBACK)).toBe('genesis_denied');
    expect(formatGenesisVenueError(apiRequestError(429), FALLBACK)).toMatch(/rate limit/i);
    expect(formatGenesisVenueError(apiRequestError(429), FALLBACK)).not.toMatch(/\b429\b/);
    expect(formatGenesisVenueError(apiRequestError(403), FALLBACK)).not.toMatch(/API Error/i);
  });

  it('falls back on TypeError / Network Error', () => {
    expect(formatGenesisVenueError(new TypeError('Failed to fetch'), FALLBACK)).toBe(FALLBACK);
    expect(formatGenesisVenueError(new Error('Network Error'), FALLBACK)).toBe(FALLBACK);
  });
});
