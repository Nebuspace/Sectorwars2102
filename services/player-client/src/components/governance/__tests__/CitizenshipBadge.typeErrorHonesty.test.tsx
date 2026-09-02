// @vitest-environment jsdom
/**
 * LEG-3078 Soft-ORDER — CitizenshipBadge TypeError densify.
 * LEG-3405 Soft-ORDER — axios-shaped Network Error densify.
 * LEG-4017 Soft-ORDER — 403/429 densify.
 */
import { describe, it, expect } from 'vitest';
import { formatCitizenshipClaimError } from '../CitizenshipBadge';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

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

  it('falls back on axios-shaped Network Error (LEG-3405)', () => {
    const text = formatCitizenshipClaimError(new Error('Network Error'));
    expect(text).toBe('Claim failed');
    expect(text).not.toMatch(/Network Error/i);
  });
});

describe('CitizenshipBadge 403/429 densify (LEG-4017)', () => {
  it('formatCitizenshipClaimError maps 403/429 without raw transport strings', () => {
    expect(formatCitizenshipClaimError(apiRequestError(403))).toBe(
      'You do not have permission to claim citizenship here.',
    );
    expect(formatCitizenshipClaimError(apiRequestError(403, 'claim_denied'))).toBe('claim_denied');
    expect(formatCitizenshipClaimError(apiRequestError(429))).toBe(
      'Citizenship claim rate limit exceeded — wait a moment and try again.',
    );
    expect(formatCitizenshipClaimError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatCitizenshipClaimError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatCitizenshipClaimError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });
});

