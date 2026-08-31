// @vitest-environment jsdom
/**
 * LEG-3080 Soft-ORDER — TractorLockPrompt TypeError densify.
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
