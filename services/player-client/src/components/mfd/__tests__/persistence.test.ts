/**
 * @vitest-environment jsdom
 * mfd persistence — versioned localStorage envelope read/write.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { persistScreens, readPersistedPage } from '../persistence';

describe('mfd persistence', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it('returns null when nothing is stored', () => {
    expect(readPersistedPage('sidebar-a')).toBeNull();
  });

  it('persists and reads screen page ids', () => {
    persistScreens({ 'sidebar-a': 'cargo', 'sidebar-b': 'comms-crew' });
    expect(readPersistedPage('sidebar-a')).toBe('cargo');
    expect(readPersistedPage('sidebar-b')).toBe('comms-crew');
    expect(readPersistedPage('missing')).toBeNull();
  });

  it('merges subsequent persistScreens calls', () => {
    persistScreens({ 'sidebar-a': 'cargo' });
    persistScreens({ 'sidebar-b': 'nav-position' });
    expect(readPersistedPage('sidebar-a')).toBe('cargo');
    expect(readPersistedPage('sidebar-b')).toBe('nav-position');
  });

  it('ignores corrupt or wrong-version envelopes', () => {
    localStorage.setItem('sw2102.mfd.v1', '{not-json');
    expect(readPersistedPage('sidebar-a')).toBeNull();

    localStorage.setItem(
      'sw2102.mfd.v1',
      JSON.stringify({ version: 99, screens: { 'sidebar-a': 'cargo' } }),
    );
    expect(readPersistedPage('sidebar-a')).toBeNull();

    localStorage.setItem(
      'sw2102.mfd.v1',
      JSON.stringify({ version: 1, screens: { 'sidebar-a': 123 } }),
    );
    expect(readPersistedPage('sidebar-a')).toBeNull();
  });

  it('swallows localStorage write failures', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    expect(() => persistScreens({ 'sidebar-a': 'cargo' })).not.toThrow();
  });
});
