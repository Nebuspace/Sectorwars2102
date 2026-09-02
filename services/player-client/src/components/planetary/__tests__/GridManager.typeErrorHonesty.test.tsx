// @vitest-environment jsdom
/**
 * LEG-3473 Soft-ORDER — GridManager Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatGridLoadError, formatGridActionError } from '../GridManager';

const ACTION_FALLBACK = 'Grid action failed';


const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};
describe('GridManager TypeError densify (LEG-3473)', () => {
  it('formatGridLoadError falls back on TypeError network collapse', () => {
    const text = formatGridLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to load planet grid');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatGridActionError falls back on TypeError network collapse', () => {
    const text = formatGridActionError(new TypeError('Failed to fetch'), ACTION_FALLBACK);
    expect(text).toBe(ACTION_FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatGridLoadError(new Error('Network Error'))).toBe('Failed to load planet grid');
    expect(formatGridLoadError(new Error('Failed to fetch'))).toBe('Failed to load planet grid');
    expect(formatGridActionError(new Error('Network Error'), ACTION_FALLBACK)).toBe(ACTION_FALLBACK);
    expect(formatGridActionError(new Error('Failed to fetch'), ACTION_FALLBACK)).toBe(
      ACTION_FALLBACK,
    );
    expect(formatGridLoadError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatGridLoadError(new Error('grid_offline'))).toBe('grid_offline');
    expect(formatGridActionError(new Error('plot_busy'), ACTION_FALLBACK)).toBe('plot_busy');
  });
});

describe('formatGridLoadError / formatGridActionError 403/429 densify (LEG-4034)', () => {
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatGridLoadError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatGridLoadError(apiRequestError(403, 'grid_denied'))).toBe('grid_denied');
    expect(formatGridLoadError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatGridActionError(apiRequestError(403), ACTION_FALLBACK)).toMatch(/permission/i);
    expect(formatGridActionError(apiRequestError(403, 'plot_denied'), ACTION_FALLBACK)).toBe('plot_denied');
    expect(formatGridActionError(apiRequestError(429), ACTION_FALLBACK)).toMatch(/rate limit/i);
    expect(formatGridActionError(apiRequestError(429), ACTION_FALLBACK)).not.toMatch(/\b429\b/);
  });
});
