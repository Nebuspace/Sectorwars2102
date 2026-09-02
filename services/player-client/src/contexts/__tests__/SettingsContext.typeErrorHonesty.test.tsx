// @vitest-environment jsdom
/**
 * LEG-3797 Soft-ORDER — SettingsContext persistence TypeError densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  SettingsProvider,
  useSettings,
  SETTINGS_PERSISTENCE_FALLBACKS,
  formatSettingsPersistenceError,
  type SettingsContextType,
} from '../SettingsContext';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let latest: SettingsContextType | null = null;

function Harness() {
  latest = useSettings();
  return latest.settingsSyncError ? (
    <div data-testid="settings-sync-error">{latest.settingsSyncError}</div>
  ) : null;
}

const assertNoTransportLeak = (text: string) => {
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
  expect(text).not.toMatch(/Network Error/i);
};

describe('formatSettingsPersistenceError (LEG-3797)', () => {
  it.each([
    ['TypeError', new TypeError('Failed to fetch'), 'load'],
    ['Network Error', new Error('Network Error'), 'save'],
    ['Failed to fetch', new Error('Failed to fetch'), 'load'],
  ] as const)('collapses %s for %s context without raw transport text', (_label, err, context) => {
    const fallback = SETTINGS_PERSISTENCE_FALLBACKS[context];
    const text = formatSettingsPersistenceError(err, fallback);
    expect(text).toBe(fallback);
    assertNoTransportLeak(text);
  });

  it('preserves non-transport server detail', () => {
    expect(
      formatSettingsPersistenceError(
        new Error('settings profile locked'),
        SETTINGS_PERSISTENCE_FALLBACKS.load,
      ),
    ).toBe('settings profile locked');
  });
});

describe('SettingsContext sync typeErrorHonesty (LEG-3797)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(async () => {
    localStorage.clear();
    latest = null;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(
        <SettingsProvider>
          <Harness />
        </SettingsProvider>,
      );
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it.each([
    ['TypeError load', new TypeError('Failed to fetch'), 'load' as const, SETTINGS_PERSISTENCE_FALLBACKS.load],
    ['Network Error save', new Error('Network Error'), 'save' as const, SETTINGS_PERSISTENCE_FALLBACKS.save],
    ['Failed to fetch load', new Error('Failed to fetch'), 'load' as const, SETTINGS_PERSISTENCE_FALLBACKS.load],
  ])('reportSettingsSyncFailure %s surfaces player-safe fallback', async (_label, err, context, expected) => {
    await act(async () => {
      latest!.reportSettingsSyncFailure(err, context);
    });

    const node = container.querySelector('[data-testid="settings-sync-error"]');
    expect(node?.textContent).toBe(expected);
    assertNoTransportLeak(node?.textContent ?? '');
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });
});

describe('formatSettingsPersistenceError 403/429 densify (LEG-4098)', () => {
  const apiRequestError = (status: number, message?: string) => {
    const err = new Error(message ?? `API Error: ${status}`);
    (err as { status?: number }).status = status;
    return err;
  };
  it('surfaces 403/429 without raw status codes', () => {
    const fallback = SETTINGS_PERSISTENCE_FALLBACKS.load;
    expect(formatSettingsPersistenceError(apiRequestError(403), fallback)).toMatch(/permission/i);
    expect(formatSettingsPersistenceError(apiRequestError(403, 'settings_denied'), fallback)).toBe(
      'settings_denied',
    );
    expect(formatSettingsPersistenceError(apiRequestError(429), fallback)).toMatch(/rate limit/i);
    expect(formatSettingsPersistenceError(apiRequestError(429), fallback)).not.toMatch(/\b429\b/);
    expect(formatSettingsPersistenceError(apiRequestError(403), fallback)).not.toMatch(/TypeError/i);
  });
});

