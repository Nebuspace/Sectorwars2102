// @vitest-environment jsdom
/**
 * LEG-3134 Soft-ORDER — TradingInterface TypeError densify.
 */
import { describe, expect, it } from 'vitest';
import {
  formatTradingBumpError,
  formatTradingDockError,
  formatTradingExecuteError,
} from '../TradingInterface';

describe('TradingInterface TypeError densify (LEG-3134)', () => {
  it('formatTradingExecuteError falls back on TypeError network collapse', () => {
    const text = formatTradingExecuteError(new TypeError('Failed to fetch'), 'ore');
    expect(text).toBe('Failed to execute trade');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatTradingExecuteError preserves server detail when not TypeError', () => {
    const err = Object.assign(new Error('insufficient credits'), {
      response: { data: { detail: 'Not enough credits for this trade.' } },
    });
    expect(formatTradingExecuteError(err, 'ore')).toBe('Not enough credits for this trade.');
  });

  it('formatTradingExecuteError maps does-not-sell hint for server copy', () => {
    const err = {
      response: { data: { detail: 'Station does not sell ore' } },
    };
    expect(formatTradingExecuteError(err, 'ore')).toMatch(/doesn't sell Ore/i);
  });

  it('formatTradingDockError falls back on TypeError network collapse', () => {
    const text = formatTradingDockError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to dock at station.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatTradingBumpError falls back on TypeError network collapse', () => {
    const text = formatTradingBumpError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Bump failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});
