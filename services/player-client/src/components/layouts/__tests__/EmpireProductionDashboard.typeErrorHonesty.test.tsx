// @vitest-environment jsdom
/**
 * LEG-3459 Soft-ORDER — EmpireProductionDashboard Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatEmpireProductionLoadError } from '../EmpireProductionDashboard';

const FALLBACK = 'Failed to load production data';

describe('EmpireProductionDashboard TypeError densify (LEG-3459)', () => {
  it('formatEmpireProductionLoadError falls back on TypeError network collapse', () => {
    const text = formatEmpireProductionLoadError(new TypeError('Failed to fetch'), FALLBACK);
    expect(text).toBe(FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatEmpireProductionLoadError(new Error('Network Error'), FALLBACK)).toBe(FALLBACK);
    expect(formatEmpireProductionLoadError(new Error('Failed to fetch'), FALLBACK)).toBe(FALLBACK);
    expect(formatEmpireProductionLoadError(new Error('Network Error'), FALLBACK)).not.toMatch(
      /Network Error/i,
    );
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatEmpireProductionLoadError(new Error('production_offline'), FALLBACK)).toBe(
      'production_offline',
    );
  });
});
