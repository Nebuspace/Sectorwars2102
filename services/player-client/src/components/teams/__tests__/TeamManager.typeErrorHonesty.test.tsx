// @vitest-environment jsdom
/**
 * LEG-3087 Soft-ORDER — TeamManager TypeError densify.
 */
import { describe, it, expect } from 'vitest';
import { formatTeamManagerLoadError, formatTeamManagerMutationError } from '../TeamManager';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

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

  it('formatTeamManagerLoad/Mutation fall back on axios Network Error / Failed to fetch (LEG-3345)', () => {
    expect(formatTeamManagerLoadError(new Error('Network Error'))).toMatch(/Failed to load team data/i);
    expect(formatTeamManagerLoadError(new Error('Failed to fetch'))).toMatch(/Failed to load team data/i);
    expect(formatTeamManagerMutationError(new Error('Network Error'), 'Failed to create team')).toBe(
      'Failed to create team',
    );
    expect(formatTeamManagerMutationError(new Error('insufficient credits'), 'Failed to create team')).toBe(
      'insufficient credits',
    );
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatTeamManagerMutationError(new Error('insufficient credits'), 'Failed to create team')).toBe(
      'insufficient credits',
    );
  });

  it('surfaces 403/429 status paths and preserves server detail on mutation (LEG-3947)', () => {
    expect(formatTeamManagerMutationError(apiRequestError(403), 'Failed to create team')).toBe(
      'You do not have permission for this team action.',
    );
    expect(formatTeamManagerMutationError(apiRequestError(429), 'Failed to create team')).toBe(
      'Team action rate limit exceeded — wait a moment and try again.',
    );
    expect(formatTeamManagerMutationError(apiRequestError(403, 'team_create_denied'), 'Failed to create team')).toBe(
      'team_create_denied',
    );
    expect(formatTeamManagerMutationError(apiRequestError(429), 'Failed to create team')).not.toMatch(/\b429\b/);
    expect(formatTeamManagerMutationError(apiRequestError(403), 'Failed to create team')).not.toMatch(/TypeError/i);
    expect(formatTeamManagerMutationError(apiRequestError(403), 'Failed to create team')).not.toMatch(/Network Error/i);
  });

  it('surfaces 403/429 status paths on load without raw transport text (LEG-3947)', () => {
    expect(formatTeamManagerLoadError(apiRequestError(403))).toBe('You are not a member of this team.');
    expect(formatTeamManagerLoadError(apiRequestError(429))).toBe(
      'Team lookup rate limit exceeded — wait a moment and try again.',
    );
    expect(formatTeamManagerLoadError(apiRequestError(403, 'team_load_denied'))).toBe('team_load_denied');
  });
});
