// @vitest-environment jsdom
/**
 * LEG-3425 Soft-ORDER — ProposePolicyForm Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatProposePolicyError } from '../ProposePolicyForm';

describe('ProposePolicyForm TypeError densify (LEG-3425)', () => {
  it('formatProposePolicyError falls back on TypeError network collapse', () => {
    const text = formatProposePolicyError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to propose policy.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatProposePolicyError(new Error('Network Error'))).toBe('Failed to propose policy.');
    expect(formatProposePolicyError(new Error('Failed to fetch'))).toBe('Failed to propose policy.');
    expect(formatProposePolicyError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatProposePolicyError(new Error('policy_quota_exceeded'))).toBe(
      'policy_quota_exceeded',
    );
  });
});
