// @vitest-environment jsdom
/**
 * LEG-3079 Soft-ORDER — ResourceSharing TypeError densify.
 */
import { describe, it, expect } from 'vitest';
import { formatTreasuryOpError } from '../ResourceSharing';

describe('ResourceSharing TypeError densify (LEG-3079)', () => {
  it('formatTreasuryOpError falls back on TypeError network collapse', () => {
    const text = formatTreasuryOpError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Operation failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatTreasuryOpError(new Error('treasury_denied'))).toBe('treasury_denied');
  });
});
