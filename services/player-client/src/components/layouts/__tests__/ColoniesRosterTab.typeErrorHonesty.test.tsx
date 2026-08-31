// @vitest-environment jsdom
/**
 * LEG-3458 Soft-ORDER — ColoniesRosterTab Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatColoniesRosterLoadError } from '../ColoniesRosterTab';

const FALLBACK = 'Failed to load colonies';

describe('ColoniesRosterTab TypeError densify (LEG-3458)', () => {
  it('formatColoniesRosterLoadError falls back on TypeError network collapse', () => {
    const text = formatColoniesRosterLoadError(new TypeError('Failed to fetch'), FALLBACK);
    expect(text).toBe(FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatColoniesRosterLoadError(new Error('Network Error'), FALLBACK)).toBe(FALLBACK);
    expect(formatColoniesRosterLoadError(new Error('Failed to fetch'), FALLBACK)).toBe(FALLBACK);
    expect(formatColoniesRosterLoadError(new Error('Network Error'), FALLBACK)).not.toMatch(
      /Network Error/i,
    );
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatColoniesRosterLoadError(new Error('colonies_unavailable'), FALLBACK)).toBe(
      'colonies_unavailable',
    );
  });
});
