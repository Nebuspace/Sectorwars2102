// @vitest-environment jsdom
/**
 * LEG-3071 Soft-ORDER — formatAssistanceLevelError TypeError densify.
 * LEG-3557 Soft-ORDER — Network Error densify.
 * Load/update must not surface raw Failed to fetch / TypeError / Network Error.
  * LEG-4015 Soft-ORDER — 403/429 densify.
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

  it('falls back on axios Network Error / Failed to fetch (LEG-3557)', () => {
    expect(formatAssistanceLevelError(new Error('Network Error'), 'load')).toBe(
      'Failed to load ARIA assistance level',
    );
    expect(formatAssistanceLevelError(new Error('Failed to fetch'), 'update')).toBe(
      'Failed to update ARIA assistance level',
    );
    expect(formatAssistanceLevelError(new Error('Network Error'), 'load')).not.toMatch(/Network Error/i);
    expect(formatAssistanceLevelError(new Error('Failed to fetch'), 'update')).not.toMatch(
      /Failed to fetch/i,
    );
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

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('AssistanceLevelSettings 403/429 densify (LEG-4015)', () => {
  it('maps 403/429 to context fallback without transport strings', () => {
    expect(formatAssistanceLevelError(apiRequestError(403), 'load')).toBe(
      'Failed to load ARIA assistance level',
    );
    expect(formatAssistanceLevelError(apiRequestError(429), 'update')).toBe(
      'Failed to update ARIA assistance level',
    );
    expect(formatAssistanceLevelError(apiRequestError(403), 'load')).not.toMatch(/\b403\b/);
    expect(formatAssistanceLevelError(apiRequestError(429), 'update')).not.toMatch(/\b429\b/);
    expect(formatAssistanceLevelError(apiRequestError(403), 'load')).not.toMatch(/TypeError/i);
    expect(formatAssistanceLevelError(apiRequestError(403), 'load')).not.toMatch(/Network Error/i);
    expect(
      formatAssistanceLevelError(apiRequestError(403, 'assistance_denied'), 'load'),
    ).toBe('assistance_denied');
  });
});

