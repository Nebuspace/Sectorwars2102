// @vitest-environment jsdom
/**
 * LEG-3093 Soft-ORDER — PlayerTradeDesk TypeError densify.
 */
import { describe, it, expect } from 'vitest';
import { formatTradeError } from '../PlayerTradeDesk';

describe('PlayerTradeDesk TypeError densify (LEG-3093)', () => {
  it('formatTradeError falls back on TypeError network collapse', () => {
    const text = formatTradeError(new TypeError('Failed to fetch'), 'trade_refresh_failed');
    expect(text).toBe('Could not refresh the trade desk.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves snake_case reason codes when not TypeError', () => {
    expect(formatTradeError(new Error('cannot_trade_self'), 'trade_open_failed')).toBe(
      'You cannot trade with yourself.',
    );
  });
});
