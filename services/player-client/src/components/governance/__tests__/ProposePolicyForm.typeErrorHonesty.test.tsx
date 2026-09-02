// @vitest-environment jsdom
/**
 * LEG-3425 Soft-ORDER — ProposePolicyForm Network Error densify.
 * LEG-4011 Soft-ORDER — 403/429 densify.
 */
import { describe, it, expect } from 'vitest';
import { formatProposePolicyError } from '../ProposePolicyForm';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

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

describe('ProposePolicyForm 403/429 densify (LEG-4011)', () => {
  it('formatProposePolicyError maps 403/429 without raw transport strings', () => {
    expect(formatProposePolicyError(apiRequestError(403))).toBe(
      'You do not have permission to propose a policy in this region.',
    );
    expect(formatProposePolicyError(apiRequestError(403, 'region is not accepting proposals'))).toBe(
      'region is not accepting proposals',
    );
    expect(formatProposePolicyError(apiRequestError(429))).toBe(
      'Policy proposal rate limit exceeded — wait a moment and try again.',
    );
    expect(formatProposePolicyError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatProposePolicyError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatProposePolicyError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });
});
