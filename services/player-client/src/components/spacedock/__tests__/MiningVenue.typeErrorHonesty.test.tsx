// @vitest-environment jsdom
/**
 * LEG-4067 Soft-ORDER — MiningVenue 403/429 + TypeError densify (invent=0).
 */
import { describe, expect, it } from 'vitest';
import {
  MINING_VENUE_FALLBACK,
  formatMiningVenueError,
} from '../MiningVenue';

const FALLBACK = MINING_VENUE_FALLBACK;

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatMiningVenueError TypeError densify (LEG-4067)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatMiningVenueError(new TypeError('Failed to fetch'), FALLBACK);
    expect(text).toBe(FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatMiningVenueError(new Error('Network Error'), FALLBACK)).toBe(FALLBACK);
    expect(formatMiningVenueError(new Error('Failed to fetch'), FALLBACK)).toBe(FALLBACK);
    expect(formatMiningVenueError(new Error('Network Error'), FALLBACK)).not.toMatch(/Network Error/i);
  });

  it('preserves server detail for non-TypeError errors', () => {
    expect(formatMiningVenueError(new Error('not_an_asteroid_field'), FALLBACK)).toBe(
      'not_an_asteroid_field',
    );
  });
});

describe('formatMiningVenueError 403/429 densify (LEG-4067)', () => {
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatMiningVenueError(apiRequestError(403), FALLBACK)).toMatch(/permission/i);
    expect(formatMiningVenueError(apiRequestError(403, 'mining_denied'), FALLBACK)).toBe(
      'mining_denied',
    );
    expect(formatMiningVenueError(apiRequestError(429), FALLBACK)).toMatch(/rate limit/i);
    expect(formatMiningVenueError(apiRequestError(429), FALLBACK)).not.toMatch(/\b429\b/);
    expect(formatMiningVenueError(apiRequestError(403), FALLBACK)).not.toMatch(/TypeError/i);
    expect(formatMiningVenueError(apiRequestError(403), FALLBACK)).not.toMatch(/API Error/i);
  });
});
