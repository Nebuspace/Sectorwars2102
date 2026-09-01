// @vitest-environment jsdom
/**
 * LEG-3492 Soft-ORDER — LandingRightsControl Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatLandingRightsError } from '../LandingRightsControl';

describe('LandingRightsControl TypeError densify (LEG-3492)', () => {
  it('formatLandingRightsError falls back on TypeError network collapse', () => {
    const text = formatLandingRightsError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to update landing rights.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatLandingRightsError(new Error('Network Error'))).toBe(
      'Failed to update landing rights.',
    );
    expect(formatLandingRightsError(new Error('Failed to fetch'))).toBe(
      'Failed to update landing rights.',
    );
    expect(formatLandingRightsError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatLandingRightsError(new Error('whitelist exceeds max entries'))).toBe(
      'whitelist exceeds max entries',
    );
  });
});
