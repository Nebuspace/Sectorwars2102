// @vitest-environment jsdom
/**
 * LEG-3146 Soft-ORDER — TacticalThreatPage TypeError densify + LEG-3143 nav threat rollup.
 */
import { describe, it, expect } from 'vitest';
import { formatTacticalThreatError } from '../pages/TacticalThreatPage';
import { formatNavThreatError } from '../navThreat';

describe('TacticalThreatPage TypeError densify (LEG-3146)', () => {
  it('formatTacticalThreatError falls back on TypeError network collapse', () => {
    const text = formatTacticalThreatError(
      new TypeError('Failed to fetch'),
      'Law status unavailable — check your connection.',
    );
    expect(text).toBe('Law status unavailable — check your connection.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves non-generic Error.message when not TypeError', () => {
    expect(formatTacticalThreatError(new Error('Fine payment declined'), 'fallback')).toBe(
      'Fine payment declined',
    );
  });
});

describe('nav threat rollup error densify (LEG-3143)', () => {
  it('formatNavThreatError hides Failed to fetch TypeError', () => {
    const text = formatNavThreatError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/check your connection/i);
    expect(text).not.toMatch(/Failed to fetch/i);
  });
});
