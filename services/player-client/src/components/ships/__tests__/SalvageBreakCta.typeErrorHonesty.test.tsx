// @vitest-environment jsdom
/**
 * LEG-3469 Soft-ORDER — SalvageBreakCta Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatSalvageBreakError } from '../SalvageBreakCta';

describe('SalvageBreakCta TypeError densify (LEG-3469)', () => {
  it('formatSalvageBreakError falls back on TypeError network collapse', () => {
    const text = formatSalvageBreakError(new TypeError('Failed to fetch'));
    expect(text).toBe('Salvage break failed — check your connection and try again.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatSalvageBreakError(new Error('Network Error'))).toBe(
      'Salvage break failed — check your connection and try again.',
    );
    expect(formatSalvageBreakError(new Error('Failed to fetch'))).toBe(
      'Salvage break failed — check your connection and try again.',
    );
    expect(formatSalvageBreakError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatSalvageBreakError(new Error('hull_locked'))).toBe('hull_locked');
  });
});
