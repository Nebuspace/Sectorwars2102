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
