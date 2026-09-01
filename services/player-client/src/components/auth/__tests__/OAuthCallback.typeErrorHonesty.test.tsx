// @vitest-environment jsdom
/**
 * LEG-3513 Soft-ORDER — OAuthCallback network collapse densify.
 */
import { describe, expect, it } from 'vitest';
import { formatOAuthCallbackError } from '../OAuthCallback';

const FALLBACK = 'Authentication failed. Please try again.';

describe('formatOAuthCallbackError network collapse densify (LEG-3513)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatOAuthCallbackError(new TypeError('Failed to fetch'));
    expect(text).toBe(FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch non-TypeError', () => {
    expect(formatOAuthCallbackError(new Error('Network Error'))).toBe(FALLBACK);
    expect(formatOAuthCallbackError(new Error('Failed to fetch'))).toBe(FALLBACK);
    expect(formatOAuthCallbackError(new Error('   '))).toBe(FALLBACK);
    expect(formatOAuthCallbackError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-network error detail', () => {
    expect(
      formatOAuthCallbackError(new Error('Invalid OAuth callback parameters: code=false')),
    ).toBe('Invalid OAuth callback parameters: code=false');
  });
});
