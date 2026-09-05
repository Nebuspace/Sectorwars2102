// @vitest-environment jsdom
/**
 * LEG-4069 Soft-ORDER — ServicesVenue 403/429 + TypeError densify (invent=0).
 */
import { describe, expect, it } from 'vitest';
import {
  SERVICES_VENUE_FALLBACK,
  formatServicesVenueError,
} from '../ServicesVenue';

const FALLBACK = SERVICES_VENUE_FALLBACK;

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatServicesVenueError TypeError densify (LEG-4069)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatServicesVenueError(new TypeError('Failed to fetch'), FALLBACK);
    expect(text).toBe(FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatServicesVenueError(new Error('Network Error'), FALLBACK)).toBe(FALLBACK);
    expect(formatServicesVenueError(new Error('Failed to fetch'), FALLBACK)).toBe(FALLBACK);
    expect(formatServicesVenueError(new Error('Network Error'), FALLBACK)).not.toMatch(/Network Error/i);
  });

  it('preserves server detail for non-TypeError errors', () => {
    expect(formatServicesVenueError(new Error('Hull systems offline.'), FALLBACK)).toBe(
      'Hull systems offline.',
    );
  });
});

describe('formatServicesVenueError 403/429 densify (LEG-4069)', () => {
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatServicesVenueError(apiRequestError(403), FALLBACK)).toMatch(/permission/i);
    expect(formatServicesVenueError(apiRequestError(403, 'repair_denied'), FALLBACK)).toBe(
      'repair_denied',
    );
    expect(formatServicesVenueError(apiRequestError(429), FALLBACK)).toMatch(/rate limit/i);
    expect(formatServicesVenueError(apiRequestError(429), FALLBACK)).not.toMatch(/\b429\b/);
    expect(formatServicesVenueError(apiRequestError(403), FALLBACK)).not.toMatch(/TypeError/i);
    expect(formatServicesVenueError(apiRequestError(403), FALLBACK)).not.toMatch(/API Error/i);
  });
});
