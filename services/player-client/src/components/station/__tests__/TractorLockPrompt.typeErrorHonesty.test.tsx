// @vitest-environment jsdom
/**
 * LEG-3080 Soft-ORDER — TractorLockPrompt TypeError densify.
 * LEG-3563 Soft-ORDER — axios Network Error densify pin.
 */
import { describe, it, expect } from 'vitest';
import { formatTractorLockActionError } from '../TractorLockPrompt';

describe('TractorLockPrompt TypeError densify (LEG-3080)', () => {
  it('formatTractorLockActionError falls back on TypeError network collapse', () => {
    const text = formatTractorLockActionError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Action failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatTractorLockActionError(new Error('break_denied'))).toBe('break_denied');
  });
});

describe('TractorLockPrompt Network Error densify (LEG-3563)', () => {
  it('falls back on axios Network Error / Failed to fetch non-TypeError', () => {
    expect(formatTractorLockActionError(new Error('Network Error'))).toBe('Action failed');
    expect(formatTractorLockActionError(new Error('Failed to fetch'))).toBe('Action failed');
    expect(formatTractorLockActionError(new Error('Network Error'))).not.toMatch(/Network Error/i);
    expect(formatTractorLockActionError(new Error('Failed to fetch'))).not.toMatch(
      /Failed to fetch/i,
    );
  });
});

describe('formatTractorLockActionError 403/429 densify (LEG-4086)', () => {
  const apiRequestError = (status: number, message?: string) => {
    const err = new Error(message ?? `API Error: ${status}`);
    (err as { status?: number }).status = status;
    return err;
  };
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatTractorLockActionError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatTractorLockActionError(apiRequestError(403, 'lock_denied'))).toBe('lock_denied');
    expect(formatTractorLockActionError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatTractorLockActionError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatTractorLockActionError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
