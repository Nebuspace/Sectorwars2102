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

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('LandingRightsControl 403/429 densify (LEG-4005)', () => {
  it('formatLandingRightsError maps 403/429 without raw transport strings', () => {
    expect(formatLandingRightsError(apiRequestError(403))).toBe(
      'You do not have permission to change landing rights.',
    );
    expect(formatLandingRightsError(apiRequestError(403, 'landing_rights_denied'))).toBe(
      'landing_rights_denied',
    );
    expect(formatLandingRightsError(apiRequestError(429))).toBe(
      'Landing-rights update rate limit exceeded — wait a moment and try again.',
    );
    expect(formatLandingRightsError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatLandingRightsError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatLandingRightsError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });
});
