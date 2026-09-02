// @vitest-environment jsdom
/**
 * LEG-3424 Soft-ORDER — PolicyCard Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatPolicyVoteError } from '../PolicyCard';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

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

  it('surfaces 403/429 status paths and preserves server detail (LEG-3950)', () => {
    expect(formatPolicyVoteError(apiRequestError(403))).toBe('You are not allowed to vote on this policy.');
    expect(formatPolicyVoteError(apiRequestError(429))).toBe(
      'Vote rate limit exceeded — wait a moment and try again.',
    );
    expect(formatPolicyVoteError(apiRequestError(403, 'policy_vote_denied'))).toBe('policy_vote_denied');
    expect(formatPolicyVoteError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatPolicyVoteError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatPolicyVoteError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });
});
