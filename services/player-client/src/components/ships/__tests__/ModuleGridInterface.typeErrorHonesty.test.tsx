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

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

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

describe('ModuleGridInterface 403/429 densify (LEG-3987)', () => {
  it('formatModuleGridLoadError surfaces 403 without raw status codes', () => {
    expect(formatModuleGridLoadError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatModuleGridLoadError(apiRequestError(403, 'module_view_denied'))).toBe(
      'module_view_denied',
    );
    expect(formatModuleGridLoadError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatModuleGridLoadError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });

  it('formatModuleGridActionError surfaces 403/429 without raw status codes', () => {
    expect(formatModuleGridActionError(apiRequestError(403), ACTION_FALLBACK)).toMatch(/permission/i);
    expect(formatModuleGridActionError(apiRequestError(403, 'install_denied'), ACTION_FALLBACK)).toBe(
      'install_denied',
    );
    expect(formatModuleGridActionError(apiRequestError(429), ACTION_FALLBACK)).toMatch(/rate limit|too many/i);
    expect(formatModuleGridActionError(apiRequestError(429), ACTION_FALLBACK)).not.toMatch(/\b429\b/);
    expect(formatModuleGridActionError(apiRequestError(403), ACTION_FALLBACK)).not.toMatch(
      /TypeError/i,
    );
  });
});
