// @vitest-environment jsdom
/**
 * LEG-3091 Soft-ORDER — GovSummaryTab TypeError densify.
 */
import { describe, it, expect } from 'vitest';
import { formatGovSummaryLoadError } from './GovSummaryTab';

describe('GovSummaryTab TypeError densify (LEG-3091)', () => {
  it('formatGovSummaryLoadError falls back on TypeError network collapse', () => {
    const text = formatGovSummaryLoadError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Failed to load governance data/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatGovSummaryLoadError(new Error('ERR_NOT_A_MEMBER'))).toBe('ERR_NOT_A_MEMBER');
  });
});
