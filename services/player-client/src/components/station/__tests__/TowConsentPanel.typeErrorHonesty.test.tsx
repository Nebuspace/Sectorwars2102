// @vitest-environment jsdom
/**
 * LEG-3089 Soft-ORDER — TowConsentPanel TypeError densify.
 */
import { describe, it, expect } from 'vitest';
import { formatTowActionError } from '../TowConsentPanel';

describe('TowConsentPanel TypeError densify (LEG-3089)', () => {
  it('formatTowActionError falls back on TypeError network collapse', () => {
    const text = formatTowActionError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Tow action failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatTowActionError(new Error('tow_target_unavailable'))).toBe('tow_target_unavailable');
  });

  it('formatTowActionError falls back on axios Network Error / Failed to fetch (LEG-3364)', () => {
    expect(formatTowActionError(new Error('Network Error'))).toBe('Tow action failed');
    expect(formatTowActionError(new Error('Failed to fetch'))).toBe('Tow action failed');
    expect(formatTowActionError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });
});
