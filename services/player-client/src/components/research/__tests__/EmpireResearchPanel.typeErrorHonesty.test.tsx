// @vitest-environment jsdom
/**
 * LEG-3433 Soft-ORDER — EmpireResearchPanel Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import {
  formatEmpireResearchLoadError,
  formatEmpireResearchMutationError,
} from '../EmpireResearchPanel';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

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

describe('EmpireResearchPanel 403/429 densify (LEG-3980)', () => {
  it('formatEmpireResearchLoadError surfaces 403/429 without raw status codes', () => {
    expect(formatEmpireResearchLoadError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatEmpireResearchLoadError(apiRequestError(403, 'research_view_denied'))).toBe(
      'research_view_denied',
    );
    expect(formatEmpireResearchLoadError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatEmpireResearchLoadError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatEmpireResearchLoadError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });

  it('formatEmpireResearchMutationError surfaces 403/429 without raw status codes', () => {
    expect(formatEmpireResearchMutationError(apiRequestError(403), 'Research action failed')).toMatch(
      /permission/i,
    );
    expect(
      formatEmpireResearchMutationError(apiRequestError(403, 'research_denied'), 'Research action failed'),
    ).toBe('research_denied');
    expect(formatEmpireResearchMutationError(apiRequestError(429), 'Research action failed')).toMatch(
      /rate limit/i,
    );
    expect(formatEmpireResearchMutationError(apiRequestError(429), 'Research action failed')).not.toMatch(
      /\b429\b/,
    );
    expect(
      formatEmpireResearchMutationError(apiRequestError(403), 'Research action failed'),
    ).not.toMatch(/TypeError/i);
  });
});
