// @vitest-environment jsdom
/**
 * LEG-3463 Soft-ORDER — ReputationPage Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatReputationLoadError } from './ReputationPage';

describe('ReputationPage TypeError densify (LEG-3463)', () => {
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
