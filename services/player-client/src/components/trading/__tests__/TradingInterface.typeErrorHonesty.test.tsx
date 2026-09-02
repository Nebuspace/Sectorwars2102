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

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

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

  it('formatTradingExecuteError falls back on Error Network Error (LEG-3505)', () => {
    const text = formatTradingExecuteError(new Error('Network Error'), 'ore');
    expect(text).toBe('Failed to execute trade');
    expect(text).not.toMatch(/Network Error/i);
  });

  it('formatTradingDockError falls back on Error Network Error (LEG-3505)', () => {
    const text = formatTradingDockError(new Error('Network Error'));
    expect(text).toBe('Failed to dock at station.');
    expect(text).not.toMatch(/Network Error/i);
  });

  it('formatTradingBumpError falls back on Error Network Error (LEG-3505)', () => {
    const text = formatTradingBumpError(new Error('Network Error'));
    expect(text).toMatch(/Bump failed/i);
    expect(text).not.toMatch(/Network Error/i);
  });

  it('surfaces 403/429 status paths and preserves server detail (LEG-3949)', () => {
    expect(formatTradingExecuteError(apiRequestError(403), 'ore')).toBe(
      'You do not have permission to trade here.',
    );
    expect(formatTradingExecuteError(apiRequestError(429), 'ore')).toBe(
      'Trade rate limit exceeded — wait a moment and try again.',
    );
    expect(formatTradingExecuteError(apiRequestError(403, 'trade_denied'), 'ore')).toBe('trade_denied');

    expect(formatTradingDockError(apiRequestError(403))).toBe('You do not have permission to dock here.');
    expect(formatTradingDockError(apiRequestError(429))).toBe(
      'Dock rate limit exceeded — wait a moment and try again.',
    );

    expect(formatTradingBumpError(apiRequestError(403))).toBe('You do not have permission to bump this slip.');
    expect(formatTradingBumpError(apiRequestError(429))).toBe(
      'Bump rate limit exceeded — wait a moment and try again.',
    );

    expect(formatTradingExecuteError(apiRequestError(403), 'ore')).not.toMatch(/\b403\b/);
    expect(formatTradingExecuteError(apiRequestError(429), 'ore')).not.toMatch(/HTTP 429/i);
    expect(formatTradingDockError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
