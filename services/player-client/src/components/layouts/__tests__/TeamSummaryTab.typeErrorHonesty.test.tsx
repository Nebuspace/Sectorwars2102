// @vitest-environment jsdom
/**
 * LEG-3462 Soft-ORDER — TeamSummaryTab Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatTeamSummaryLoadError } from '../TeamSummaryTab';

describe('TeamSummaryTab TypeError densify (LEG-3462)', () => {
  it('formatTeamSummaryLoadError falls back on TypeError network collapse', () => {
    const text = formatTeamSummaryLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to load team data');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatTeamSummaryLoadError(new Error('Network Error'))).toBe('Failed to load team data');
    expect(formatTeamSummaryLoadError(new Error('Failed to fetch'))).toBe(
      'Failed to load team data',
    );
    expect(formatTeamSummaryLoadError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatTeamSummaryLoadError(new Error('team_unavailable'))).toBe('team_unavailable');
  });
});

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatTeamSummaryLoadError 403/429 densify (LEG-4043)', () => {
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatTeamSummaryLoadError(apiRequestError(403))).toMatch(/not a member|permission/i);
    expect(formatTeamSummaryLoadError(apiRequestError(403, 'team_denied'))).toBe('team_denied');
    expect(formatTeamSummaryLoadError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatTeamSummaryLoadError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatTeamSummaryLoadError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
