// @vitest-environment jsdom
/**
 * LEG-4071 Soft-ORDER — GamblingVenue 403/429 densify (invent=0).
 */
import { describe, expect, it } from 'vitest';
import { GAMBLING_VENUE_FALLBACK, formatGamblingVenueError } from '../GamblingVenue';

const FALLBACK = GAMBLING_VENUE_FALLBACK;
const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatGamblingVenueError 403/429 densify (LEG-4071)', () => {
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatGamblingVenueError(apiRequestError(403), FALLBACK)).toMatch(/permission/i);
    expect(formatGamblingVenueError(apiRequestError(403, 'gamble_denied'), FALLBACK)).toBe('gamble_denied');
    expect(formatGamblingVenueError(apiRequestError(429), FALLBACK)).toMatch(/rate limit/i);
    expect(formatGamblingVenueError(apiRequestError(429), FALLBACK)).not.toMatch(/\b429\b/);
    expect(formatGamblingVenueError(apiRequestError(403), FALLBACK)).not.toMatch(/API Error/i);
  });

  it('falls back on TypeError / Network Error', () => {
    expect(formatGamblingVenueError(new TypeError('Failed to fetch'), FALLBACK)).toBe(FALLBACK);
    expect(formatGamblingVenueError(new Error('Network Error'), FALLBACK)).toBe(FALLBACK);
  });
});
