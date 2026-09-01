// @vitest-environment jsdom
/**
 * LEG-3622 Soft-ORDER — NavigationMap nav-threat network-collapse densify.
 * NavigationMap consumes threatBands from useNavThreatRollup; formatNavThreatError
 * is the honest fallback on GET /nav/threat transport collapse.
 */
import { describe, expect, it } from 'vitest';
import { formatNavThreatError } from '../navThreat';

const FALLBACK = 'Threat data unavailable — check your connection.';

describe('NavigationMap nav-threat TypeError densify (LEG-3622)', () => {
  it('formatNavThreatError falls back on TypeError network collapse', () => {
    const text = formatNavThreatError(new TypeError('Failed to fetch'));
    expect(text).toBe(FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatNavThreatError falls back on axios Network Error / Failed to fetch', () => {
    expect(formatNavThreatError(new Error('Network Error'))).toBe(FALLBACK);
    expect(formatNavThreatError(new Error('Failed to fetch'))).toBe(FALLBACK);
    expect(formatNavThreatError(new Error('   '))).toBe(FALLBACK);
    expect(formatNavThreatError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic server detail when not transport collapse', () => {
    expect(formatNavThreatError(new Error('Threat rollup temporarily disabled'))).toBe(
      'Threat rollup temporarily disabled',
    );
  });
});
