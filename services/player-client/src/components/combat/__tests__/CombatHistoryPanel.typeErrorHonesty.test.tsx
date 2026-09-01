// @vitest-environment jsdom
/**
 * LEG-3163 Soft-ORDER — formatCombatHistoryError TypeError/network honesty.
 * LEG-3564 Soft-ORDER — axios Network Error densify pin (helper already collapses).
 */
import { describe, it, expect } from 'vitest';
import { formatCombatHistoryError } from '../CombatHistoryPanel';

const FALLBACK = 'Failed to load combat history';

describe('formatCombatHistoryError (LEG-3163)', () => {
  it('formatCombatHistoryError(new TypeError("Failed to fetch"), fallback) returns fallback', () => {
    const text = formatCombatHistoryError(new TypeError('Failed to fetch'), FALLBACK);
    expect(text).toBe(FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves Error.message for non-TypeError failures', () => {
    expect(formatCombatHistoryError(new Error('API Error: 503'), FALLBACK)).toBe(
      'API Error: 503',
    );
  });
});

describe('formatCombatHistoryError Network Error densify (LEG-3564)', () => {
  it('falls back on axios Network Error / Failed to fetch non-TypeError', () => {
    expect(formatCombatHistoryError(new Error('Network Error'), FALLBACK)).toBe(FALLBACK);
    expect(formatCombatHistoryError(new Error('Failed to fetch'), FALLBACK)).toBe(FALLBACK);
    expect(formatCombatHistoryError(new Error('Network Error'), FALLBACK)).not.toMatch(
      /Network Error/i,
    );
    expect(formatCombatHistoryError(new Error('Failed to fetch'), FALLBACK)).not.toMatch(
      /Failed to fetch/i,
    );
  });
});
