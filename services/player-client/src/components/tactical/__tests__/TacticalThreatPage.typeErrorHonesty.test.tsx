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

  it('formatTacticalThreat/NavThreat fall back on axios Network Error (LEG-3302)', () => {
    expect(
      formatTacticalThreatError(new Error('Network Error'), 'Law status unavailable — check your connection.'),
    ).toBe('Law status unavailable — check your connection.');
    expect(
      formatTacticalThreatError(new Error('Failed to fetch'), 'Law status unavailable — check your connection.'),
    ).toBe('Law status unavailable — check your connection.');
    expect(
      formatTacticalThreatError(new Error('   '), 'Law status unavailable — check your connection.'),
    ).toBe('Law status unavailable — check your connection.');
    expect(formatNavThreatError(new Error('Network Error'))).toBe(
      'Threat data unavailable — check your connection.',
    );
    expect(formatNavThreatError(new Error('Failed to fetch'))).toBe(
      'Threat data unavailable — check your connection.',
    );
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
