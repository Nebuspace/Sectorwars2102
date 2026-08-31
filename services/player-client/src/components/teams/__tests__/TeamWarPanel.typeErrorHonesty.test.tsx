// @vitest-environment jsdom
/**
 * LEG-3075 Soft-ORDER — TeamWarPanel TypeError densify.
 * Load/action must not surface raw Failed to fetch / TypeError.
 */
import { describe, it, expect } from 'vitest';
import {
  formatTeamWarLoadError,
  formatTeamWarActionError,
} from '../TeamWarPanel';

describe('TeamWarPanel TypeError densify (LEG-3075)', () => {
  it('formatTeamWarLoadError falls back on TypeError network collapse', () => {
    const text = formatTeamWarLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to load wars');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatTeamWarActionError falls back on TypeError network collapse', () => {
    const text = formatTeamWarActionError(new TypeError('Failed to fetch'));
    expect(text).toBe('Action failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatTeamWarLoadError(new Error('wars_unavailable'))).toBe('wars_unavailable');
    expect(formatTeamWarActionError(new Error('ceasefire_denied'))).toBe('ceasefire_denied');
  });
});
