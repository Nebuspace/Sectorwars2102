// @vitest-environment jsdom
/**
 * LEG-3071 Soft-ORDER — formatAssistanceLevelError TypeError densify.
 * Load/update must not surface raw Failed to fetch / TypeError.
 */
import { describe, it, expect } from 'vitest';
import { formatAssistanceLevelError } from '../AssistanceLevelSettings';

describe('formatAssistanceLevelError (LEG-3071)', () => {
  it('falls back on TypeError network collapse for load', () => {
    const text = formatAssistanceLevelError(new TypeError('Failed to fetch'), 'load');
    expect(text).toBe('Failed to load ARIA assistance level');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on TypeError network collapse for update', () => {
    const text = formatAssistanceLevelError(new TypeError('Failed to fetch'), 'update');
    expect(text).toBe('Failed to update ARIA assistance level');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves 500 server detail when present', () => {
    const err = Object.assign(new Error('Internal server error: profile unavailable'), {
      status: 500,
    });
    expect(formatAssistanceLevelError(err, 'load')).toBe(
      'Internal server error: profile unavailable',
    );
  });
});
