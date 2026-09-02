// @vitest-environment jsdom
/**
 * LEG-3086 Soft-ORDER — BountyBoard TypeError densify.
 * LEG-4000 — 403/429 densify for formatBountyBoardLoadError (invent=0).
 */
import { describe, it, expect } from 'vitest';
import { formatBountyBoardLoadError } from '../BountyBoard';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('BountyBoard TypeError densify (LEG-3086)', () => {
  it('formatBountyBoardLoadError falls back on TypeError network collapse', () => {
    const text = formatBountyBoardLoadError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Failed to load bounty board/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatBountyBoardLoadError(new Error('board_denied'))).toBe('board_denied');
  });

  it('formatBountyBoardLoadError falls back on axios Network Error / Failed to fetch (LEG-3519)', () => {
    expect(formatBountyBoardLoadError(new Error('Network Error'))).toMatch(/Failed to load bounty board/i);
    expect(formatBountyBoardLoadError(new Error('Failed to fetch'))).toMatch(/Failed to load bounty board/i);
    expect(formatBountyBoardLoadError(new Error('Network Error'))).not.toBe('Network Error');
  });

  it('surfaces 403/429 without raw status codes (LEG-4000)', () => {
    expect(formatBountyBoardLoadError(apiRequestError(403))).toMatch(/access denied/i);
    expect(formatBountyBoardLoadError(apiRequestError(403, 'board_denied'))).toBe('board_denied');
    expect(formatBountyBoardLoadError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatBountyBoardLoadError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatBountyBoardLoadError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatBountyBoardLoadError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });
});
