// @vitest-environment jsdom
/**
 * LEG-4068 Soft-ORDER — TradingVenue 403/429 densify (invent=0).
 * Surfaces via formatTradingVenueError → LongTermMooringPanel.errMessage.
 */
import { describe, expect, it } from 'vitest';
import { formatTradingVenueError } from '../TradingVenue';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatTradingVenueError 403/429 densify (LEG-4068)', () => {
  it('maps 403/429 without raw transport leakage', () => {
    expect(formatTradingVenueError(apiRequestError(403))).toBe(
      'Access denied — you cannot request long-term mooring right now.',
    );
    expect(formatTradingVenueError(apiRequestError(403, 'mooring_denied'))).toBe('mooring_denied');
    expect(formatTradingVenueError(apiRequestError(429))).toBe(
      'Long-term mooring rate limit exceeded — wait a moment and try again.',
    );
    expect(formatTradingVenueError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatTradingVenueError(apiRequestError(403))).not.toMatch(/API Error/i);
    expect(formatTradingVenueError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });

  it('falls back on TypeError / Network Error without transport leakage', () => {
    expect(formatTradingVenueError(new TypeError('Failed to fetch'))).toBe(
      'Long-term mooring request failed',
    );
    expect(formatTradingVenueError(new Error('Network Error'))).toBe(
      'Long-term mooring request failed',
    );
    expect(formatTradingVenueError(new TypeError('Failed to fetch'))).not.toMatch(/Failed to fetch/i);
    expect(formatTradingVenueError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });
});
