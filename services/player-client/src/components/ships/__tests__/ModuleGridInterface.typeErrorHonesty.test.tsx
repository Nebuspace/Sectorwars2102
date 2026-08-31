// @vitest-environment jsdom
/**
 * LEG-3470 Soft-ORDER — ModuleGridInterface Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import {
  formatModuleGridLoadError,
  formatModuleGridActionError,
} from '../ModuleGridInterface';

const ACTION_FALLBACK = 'Module action failed';

describe('ModuleGridInterface TypeError densify (LEG-3470)', () => {
  it('formatModuleGridLoadError falls back on TypeError network collapse', () => {
    const text = formatModuleGridLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to load module data');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatModuleGridActionError falls back on TypeError network collapse', () => {
    const text = formatModuleGridActionError(new TypeError('Failed to fetch'), ACTION_FALLBACK);
    expect(text).toBe(ACTION_FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatModuleGridLoadError(new Error('Network Error'))).toBe('Failed to load module data');
    expect(formatModuleGridLoadError(new Error('Failed to fetch'))).toBe(
      'Failed to load module data',
    );
    expect(formatModuleGridActionError(new Error('Network Error'), ACTION_FALLBACK)).toBe(
      ACTION_FALLBACK,
    );
    expect(formatModuleGridActionError(new Error('Failed to fetch'), ACTION_FALLBACK)).toBe(
      ACTION_FALLBACK,
    );
    expect(formatModuleGridLoadError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatModuleGridLoadError(new Error('lattice_offline'))).toBe('lattice_offline');
    expect(formatModuleGridActionError(new Error('slot_busy'), ACTION_FALLBACK)).toBe('slot_busy');
  });
});
