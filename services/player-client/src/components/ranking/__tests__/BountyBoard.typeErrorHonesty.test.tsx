// @vitest-environment jsdom
/**
 * LEG-3086 Soft-ORDER — BountyBoard TypeError densify.
 */
import { describe, it, expect } from 'vitest';
import { formatBountyBoardLoadError } from '../BountyBoard';

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
});
