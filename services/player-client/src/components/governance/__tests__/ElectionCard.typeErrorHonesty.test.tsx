// @vitest-environment jsdom
/**
 * LEG-3423 Soft-ORDER — ElectionCard Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import {
  formatElectionVoteError,
  formatElectionCandidacyError,
} from '../ElectionCard';

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
