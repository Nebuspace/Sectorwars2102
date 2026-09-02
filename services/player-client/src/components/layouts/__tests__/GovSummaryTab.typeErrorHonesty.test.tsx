// @vitest-environment jsdom
/**
 * LEG-3460 Soft-ORDER — GovSummaryTab Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatGovSummaryLoadError } from '../GovSummaryTab';

describe('GovSummaryTab TypeError densify (LEG-3460)', () => {
  it('formatGovSummaryLoadError falls back on TypeError network collapse', () => {
    const text = formatGovSummaryLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to load governance data');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatGovSummaryLoadError(new Error('Network Error'))).toBe(
      'Failed to load governance data',
    );
    expect(formatGovSummaryLoadError(new Error('Failed to fetch'))).toBe(
      'Failed to load governance data',
    );
    expect(formatGovSummaryLoadError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatGovSummaryLoadError(new Error('governance_unavailable'))).toBe(
      'governance_unavailable',
    );
  });
});

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatGovSummaryLoadError 403/429 densify (LEG-4042)', () => {
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatGovSummaryLoadError(apiRequestError(403))).toMatch(/not a member|permission/i);
    expect(formatGovSummaryLoadError(apiRequestError(403, 'gov_denied'))).toBe('gov_denied');
    expect(formatGovSummaryLoadError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatGovSummaryLoadError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatGovSummaryLoadError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
