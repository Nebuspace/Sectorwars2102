// @vitest-environment jsdom
/**
 * LEG-3433 Soft-ORDER — EmpireResearchPanel Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import {
  formatEmpireResearchLoadError,
  formatEmpireResearchMutationError,
} from '../EmpireResearchPanel';

describe('EmpireResearchPanel TypeError densify (LEG-3433)', () => {
  it('formatEmpireResearchLoadError falls back on TypeError network collapse', () => {
    const text = formatEmpireResearchLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to load research cockpit');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatEmpireResearchMutationError falls back on axios Network Error', () => {
    expect(formatEmpireResearchMutationError(new Error('Network Error'), 'Research action failed')).toBe(
      'Research action failed',
    );
    expect(formatEmpireResearchMutationError(new Error('Failed to fetch'), 'Research action failed')).toBe(
      'Research action failed',
    );
    expect(
      formatEmpireResearchMutationError(new Error('Network Error'), 'Research action failed'),
    ).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatEmpireResearchMutationError(new Error('tier_locked'), 'Research action failed')).toBe(
      'tier_locked',
    );
  });
});
