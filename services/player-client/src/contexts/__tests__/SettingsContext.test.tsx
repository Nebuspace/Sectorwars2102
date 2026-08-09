// @vitest-environment jsdom
/**
 * SettingsContext — local-only UI preferences (currently: uiScale),
 * persisted to localStorage and applied via a CSS custom property before
 * first paint. Follows the useAnnunciatorState.test.tsx harness convention:
 * a host component captures the hook's return into a module-level slot
 * every render. localStorage is real (jsdom) and cleared each test, since
 * the module's own persistence/sanitization IS the behavior under test.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SettingsProvider, useSettings, type SettingsContextType } from '../SettingsContext';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const UI_SCALE_STORAGE_KEY = 'uiScale';

let latest: SettingsContextType | null = null;

function Harness() {
  latest = useSettings();
  return null;
}

let container: HTMLElement;
let root: ReturnType<typeof createRoot>;

const render = async (children: React.ReactNode = <Harness />) => {
  await act(async () => {
    root.render(<SettingsProvider>{children}</SettingsProvider>);
  });
};

beforeEach(() => {
  localStorage.clear();
  document.documentElement.style.removeProperty('--ui-scale');
  latest = null;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => {
    root.unmount();
  });
  container.remove();
  vi.restoreAllMocks();
});

describe('useSettings — outside a provider', () => {
  it('throws when called without a SettingsProvider ancestor', async () => {
    const errRoot = document.createElement('div');
    document.body.appendChild(errRoot);
    const errorRoot = createRoot(errRoot);
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});

    await expect(
      act(async () => {
        errorRoot.render(<Harness />);
      })
    ).rejects.toThrow('useSettings must be used within a SettingsProvider');

    spy.mockRestore();
    errRoot.remove();
  });
});

describe('SettingsProvider — initial hydration', () => {
  it('defaults uiScale to 1.0 when nothing is persisted', async () => {
    await render();
    expect(latest!.uiScale).toBe(1.0);
  });

  it('hydrates a valid persisted scale within range', async () => {
    localStorage.setItem(UI_SCALE_STORAGE_KEY, '0.8');
    await render();
    expect(latest!.uiScale).toBe(0.8);
  });

  it('clamps a persisted value above the max bound (legacy 1.5 select option)', async () => {
    localStorage.setItem(UI_SCALE_STORAGE_KEY, '1.5');
    await render();
    expect(latest!.uiScale).toBe(1.2);
  });

  it('clamps a persisted value below the min bound', async () => {
    localStorage.setItem(UI_SCALE_STORAGE_KEY, '0.1');
    await render();
    expect(latest!.uiScale).toBe(0.6);
  });

  it('falls back to default on non-numeric persisted garbage', async () => {
    localStorage.setItem(UI_SCALE_STORAGE_KEY, 'not-a-number');
    await render();
    expect(latest!.uiScale).toBe(1.0);
  });

  it('falls back to default when localStorage.getItem throws', async () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('disabled');
    });
    await render();
    expect(latest!.uiScale).toBe(1.0);
    spy.mockRestore();
  });

  it('applies the hydrated scale to the --ui-scale CSS custom property', async () => {
    localStorage.setItem(UI_SCALE_STORAGE_KEY, '0.75');
    await render();
    expect(document.documentElement.style.getPropertyValue('--ui-scale')).toBe('0.75');
  });
});

describe('setUiScale', () => {
  it('updates state and persists the new value', async () => {
    await render();
    await act(async () => {
      latest!.setUiScale(0.9);
    });
    expect(latest!.uiScale).toBe(0.9);
    expect(localStorage.getItem(UI_SCALE_STORAGE_KEY)).toBe('0.9');
  });

  it('updates the --ui-scale CSS custom property on change', async () => {
    await render();
    await act(async () => {
      latest!.setUiScale(0.9);
    });
    expect(document.documentElement.style.getPropertyValue('--ui-scale')).toBe('0.9');
  });

  it('clamps an out-of-range value above the max bound', async () => {
    await render();
    await act(async () => {
      latest!.setUiScale(3);
    });
    expect(latest!.uiScale).toBe(1.2);
    expect(localStorage.getItem(UI_SCALE_STORAGE_KEY)).toBe('1.2');
  });

  it('clamps an out-of-range value below the min bound', async () => {
    await render();
    await act(async () => {
      latest!.setUiScale(0);
    });
    expect(latest!.uiScale).toBe(0.6);
  });

  it('falls back to default on a non-finite input', async () => {
    await render();
    await act(async () => {
      latest!.setUiScale(NaN);
    });
    expect(latest!.uiScale).toBe(1.0);

    await act(async () => {
      latest!.setUiScale(Infinity);
    });
    expect(latest!.uiScale).toBe(1.0);
  });

  it('applies live even when persistence throws (best-effort write)', async () => {
    await render();
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota exceeded');
    });

    await act(async () => {
      latest!.setUiScale(0.85);
    });

    expect(latest!.uiScale).toBe(0.85);
    expect(document.documentElement.style.getPropertyValue('--ui-scale')).toBe('0.85');
    spy.mockRestore();
  });
});
