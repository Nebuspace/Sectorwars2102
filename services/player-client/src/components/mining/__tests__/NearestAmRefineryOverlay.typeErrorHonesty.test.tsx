// @vitest-environment jsdom
/**
 * LEG-3490 Soft-ORDER — NearestAmRefineryOverlay Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatNearestAmRefineryError } from '../NearestAmRefineryOverlay';

describe('NearestAmRefineryOverlay TypeError densify (LEG-3490)', () => {
  it('formatNearestAmRefineryError falls back on TypeError network collapse', () => {
    const text = formatNearestAmRefineryError(new TypeError('Failed to fetch'));
    expect(text).toBe('Nearest AM refinery lookup failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatNearestAmRefineryError(new Error('Network Error'))).toBe(
      'Nearest AM refinery lookup failed',
    );
    expect(formatNearestAmRefineryError(new Error('Failed to fetch'))).toBe(
      'Nearest AM refinery lookup failed',
    );
    expect(formatNearestAmRefineryError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatNearestAmRefineryError(new Error('no_am_station_in_range'))).toBe(
      'no_am_station_in_range',
    );
  });
});
