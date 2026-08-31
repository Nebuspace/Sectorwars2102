// @vitest-environment jsdom
/**
 * LEG-3078 Soft-ORDER — CitizenshipBadge TypeError densify.
 */
import { describe, it, expect } from 'vitest';
import { formatCitizenshipClaimError } from '../CitizenshipBadge';

describe('CitizenshipBadge TypeError densify (LEG-3078)', () => {
  it('formatCitizenshipClaimError falls back on TypeError network collapse', () => {
    const text = formatCitizenshipClaimError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Claim failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatCitizenshipClaimError(new Error('claim_denied'))).toBe('claim_denied');
  });
});
