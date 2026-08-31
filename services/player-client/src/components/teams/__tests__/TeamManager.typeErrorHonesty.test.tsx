// @vitest-environment jsdom
/**
 * LEG-3087 Soft-ORDER — TeamManager TypeError densify.
 */
import { describe, it, expect } from 'vitest';
import { formatTeamManagerLoadError, formatTeamManagerMutationError } from '../TeamManager';

describe('TeamManager TypeError densify (LEG-3087)', () => {
  it('formatTeamManagerLoadError falls back on TypeError network collapse', () => {
    const text = formatTeamManagerLoadError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Failed to load team data/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatTeamManagerMutationError falls back on TypeError network collapse', () => {
    const text = formatTeamManagerMutationError(
      new TypeError('Failed to fetch'),
      'Failed to create team',
    );
    expect(text).toBe('Failed to create team');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatTeamManagerMutationError(new Error('insufficient credits'), 'Failed to create team')).toBe(
      'insufficient credits',
    );
  });
});
