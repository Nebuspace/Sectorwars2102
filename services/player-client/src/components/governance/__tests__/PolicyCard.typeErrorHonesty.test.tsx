// @vitest-environment jsdom
/**
 * LEG-3424 Soft-ORDER — PolicyCard Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatPolicyVoteError } from '../PolicyCard';

describe('PolicyCard TypeError densify (LEG-3424)', () => {
  it('formatPolicyVoteError falls back on TypeError network collapse', () => {
    const text = formatPolicyVoteError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to cast vote.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatPolicyVoteError(new Error('Network Error'))).toBe('Failed to cast vote.');
    expect(formatPolicyVoteError(new Error('Failed to fetch'))).toBe('Failed to cast vote.');
    expect(formatPolicyVoteError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatPolicyVoteError(new Error('already_voted'))).toBe('already_voted');
  });
});
