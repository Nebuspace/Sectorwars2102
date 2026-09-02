// @vitest-environment jsdom
/**
 * LEG-3423 Soft-ORDER — ElectionCard Network Error densify.
 * LEG-4010 Soft-ORDER — 403/429 densify.
 */
import { describe, it, expect } from 'vitest';
import {
  formatElectionVoteError,
  formatElectionCandidacyError,
} from '../ElectionCard';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('ElectionCard TypeError densify (LEG-3423)', () => {
  it('formatElectionVoteError falls back on TypeError network collapse', () => {
    const text = formatElectionVoteError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to cast vote.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatElectionCandidacyError falls back on TypeError network collapse', () => {
    const text = formatElectionCandidacyError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to register candidacy.');
    expect(text).not.toMatch(/Failed to fetch/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatElectionVoteError(new Error('Network Error'))).toBe('Failed to cast vote.');
    expect(formatElectionCandidacyError(new Error('Network Error'))).toBe(
      'Failed to register candidacy.',
    );
    expect(formatElectionVoteError(new Error('Failed to fetch'))).toBe('Failed to cast vote.');
    expect(formatElectionVoteError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatElectionVoteError(new Error('not_a_citizen'))).toBe('not_a_citizen');
    expect(formatElectionCandidacyError(new Error('already_candidate'))).toBe('already_candidate');
  });
});

describe('ElectionCard 403/429 densify (LEG-4010)', () => {
  it('formatElectionVoteError maps 403/429 without raw transport strings', () => {
    expect(formatElectionVoteError(apiRequestError(403))).toBe(
      'You are not allowed to vote in this election.',
    );
    expect(formatElectionVoteError(apiRequestError(403, 'vote_denied_by_region'))).toBe(
      'vote_denied_by_region',
    );
    expect(formatElectionVoteError(apiRequestError(429))).toBe(
      'Vote rate limit exceeded — wait a moment and try again.',
    );
    expect(formatElectionVoteError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatElectionVoteError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatElectionVoteError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });

  it('formatElectionCandidacyError maps 403/429 without raw transport strings', () => {
    expect(formatElectionCandidacyError(apiRequestError(403))).toBe(
      'You are not allowed to register as a candidate.',
    );
    expect(formatElectionCandidacyError(apiRequestError(403, 'candidacy_denied'))).toBe(
      'candidacy_denied',
    );
    expect(formatElectionCandidacyError(apiRequestError(429))).toBe(
      'Candidacy rate limit exceeded — wait a moment and try again.',
    );
    expect(formatElectionCandidacyError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatElectionCandidacyError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatElectionCandidacyError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });
});
