// @vitest-environment jsdom
/**
 * LEG-3778 Soft-ORDER — ReputationPage TypeError/network densify.
 * LEG-4016 Soft-ORDER — 403/429 densify.
 */
import { describe, it, expect } from 'vitest';
import { formatReputationLoadError } from '../ReputationPage';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('ReputationPage TypeError densify (LEG-3778)', () => {
  it('formatReputationLoadError falls back on TypeError network collapse', () => {
    const text = formatReputationLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to load faction standings');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatReputationLoadError(new Error('Network Error'))).toBe(
      'Failed to load faction standings',
    );
    expect(formatReputationLoadError(new Error('Failed to fetch'))).toBe(
      'Failed to load faction standings',
    );
    expect(formatReputationLoadError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatReputationLoadError(new Error('standings_unavailable'))).toBe(
      'standings_unavailable',
    );
  });
});

describe('ReputationPage 403/429 densify (LEG-4016)', () => {
  it('formatReputationLoadError maps 403/429 without raw transport strings', () => {
    expect(formatReputationLoadError(apiRequestError(403))).toBe(
      'Access denied — faction standings are not available right now.',
    );
    expect(formatReputationLoadError(apiRequestError(403, 'standings_denied'))).toBe(
      'standings_denied',
    );
    expect(formatReputationLoadError(apiRequestError(429))).toBe(
      'Faction standings rate limit exceeded — wait a moment and try again.',
    );
    expect(formatReputationLoadError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatReputationLoadError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatReputationLoadError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });
});

