// @vitest-environment jsdom
/**
 * LEG-3073 Soft-ORDER — CommsCrewPage TypeError densify.
 * Threads/flag/send/purge must not surface raw Failed to fetch / TypeError.
 */
import { describe, it, expect } from 'vitest';
import {
  formatCommsThreadsLoadError,
  formatCommsFlagError,
  formatCommsSendError,
  formatCommsPurgeError,
} from './CommsCrewPage';

describe('CommsCrewPage TypeError densify (LEG-3073)', () => {
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

  it('formatCommsThreadsLoad/Send/Flag/Purge fall back on axios Network Error / Failed to fetch (LEG-3353)', () => {
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
