// @vitest-environment jsdom
/**
 * LEG-3777 Soft-ORDER — CommsCrewPage TypeError densify.
 * LEG-4014 Soft-ORDER — 403/429 densify.
 */
import { describe, it, expect } from 'vitest';
import {
  formatCommsThreadsLoadError,
  formatCommsFlagError,
  formatCommsSendError,
  formatCommsPurgeError,
} from '../CommsCrewPage';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('CommsCrewPage TypeError densify (LEG-3777)', () => {
  it('formatCommsThreadsLoadError falls back on TypeError network collapse', () => {
    const text = formatCommsThreadsLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to load threads');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatCommsFlagError falls back on TypeError network collapse', () => {
    const text = formatCommsFlagError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to flag transmission');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatCommsSendError falls back on TypeError network collapse', () => {
    const text = formatCommsSendError(new TypeError('Failed to fetch'));
    expect(text).toBe('TRANSMISSION FAILED');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatCommsPurgeError falls back on TypeError network collapse', () => {
    const text = formatCommsPurgeError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to purge transmission');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatCommsThreadsLoad/Send/Flag/Purge fall back on axios Network Error / Failed to fetch', () => {
    expect(formatCommsThreadsLoadError(new Error('Network Error'))).toBe('Failed to load threads');
    expect(formatCommsThreadsLoadError(new Error('Failed to fetch'))).toBe('Failed to load threads');
    expect(formatCommsThreadsLoadError(new Error('Network Error'))).not.toBe('Network Error');

    expect(formatCommsSendError(new Error('Network Error'))).toBe('TRANSMISSION FAILED');
    expect(formatCommsSendError(new Error('Failed to fetch'))).toBe('TRANSMISSION FAILED');
    expect(formatCommsSendError(new Error('Network Error'))).not.toBe('Network Error');

    expect(formatCommsFlagError(new Error('Network Error'))).toBe('Failed to flag transmission');
    expect(formatCommsFlagError(new Error('Failed to fetch'))).toBe('Failed to flag transmission');

    expect(formatCommsPurgeError(new Error('Network Error'))).toBe('Failed to purge transmission');
    expect(formatCommsPurgeError(new Error('Failed to fetch'))).toBe('Failed to purge transmission');
  });

  it('preserves 500/403 detail paths when HTTP status is present', () => {
    const err500 = Object.assign(new Error('API Error: 500'), { status: 500 });
    expect(formatCommsThreadsLoadError(err500)).toBe('Failed to load threads');

    const err403Detail = Object.assign(new Error('crew_comms_denied'), { status: 403 });
    expect(formatCommsThreadsLoadError(err403Detail)).toBe('crew_comms_denied');
    expect(formatCommsFlagError(err403Detail)).toBe('crew_comms_denied');
  });
});

describe('CommsCrewPage 403/429 densify (LEG-4014)', () => {
  it('formatCommsFlagError / formatCommsThreadsLoadError map 403/429', () => {
    expect(formatCommsFlagError(apiRequestError(403))).toBe(
      'Access denied — you cannot flag transmissions right now.',
    );
    expect(formatCommsFlagError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatCommsFlagError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatCommsThreadsLoadError(apiRequestError(403))).toBe(
      'Access denied — you cannot view threads right now.',
    );
    expect(formatCommsThreadsLoadError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatCommsThreadsLoadError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatCommsThreadsLoadError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });

  it('formatCommsSendError / formatCommsPurgeError map 403/429', () => {
    expect(formatCommsSendError(apiRequestError(403))).toBe(
      'Access denied — you cannot send transmissions right now.',
    );
    expect(formatCommsSendError(apiRequestError(429))).toMatch(/Too many messages|rate limit|5 per 60s/i);
    expect(formatCommsPurgeError(apiRequestError(403))).toBe(
      'Access denied — you cannot purge transmissions right now.',
    );
    expect(formatCommsPurgeError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatCommsPurgeError(apiRequestError(429))).not.toMatch(/\b429\b/);
  });
});

