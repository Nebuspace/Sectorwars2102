// @vitest-environment jsdom
/**
 * ThemeProvider — theme registry (currently: cockpit only), localStorage
 * hydration/persistence, and CSS-variable/body-class application. Follows
 * the useAnnunciatorState.test.tsx harness convention: a host component
 * captures useTheme()'s return into a module-level slot every render.
 * localStorage/document state are real (jsdom) and reset each test.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { ThemeProvider, useTheme, useThemeColors, useThemeFonts } from '../ThemeProvider';
import { cockpitTheme } from '../themes/cockpit';
import type { ThemeName } from '../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let latest: ReturnType<typeof useTheme> | null = null;
let latestColors: ReturnType<typeof useThemeColors> | null = null;
let latestFonts: ReturnType<typeof useThemeFonts> | null = null;

function Harness() {
  latest = useTheme();
  latestColors = useThemeColors();
  latestFonts = useThemeFonts();
  return null;
}

let container: HTMLElement;
let root: ReturnType<typeof createRoot>;

const render = async (props: { defaultTheme?: ThemeName } = {}) => {
  await act(async () => {
    root.render(
      <ThemeProvider defaultTheme={props.defaultTheme}>
        <Harness />
      </ThemeProvider>
    );
  });
};

beforeEach(() => {
  localStorage.clear();
  document.body.className = '';
  latest = null;
  latestColors = null;
  latestFonts = null;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => {
    root.unmount();
  });
  container.remove();
});

describe('useTheme — outside a provider', () => {
  it('throws when called without a ThemeProvider ancestor', async () => {
    function Bare() {
      useTheme();
      return null;
    }
    const errRoot = document.createElement('div');
    document.body.appendChild(errRoot);
    const errorRoot = createRoot(errRoot);

    await expect(
      act(async () => {
        errorRoot.render(<Bare />);
      })
    ).rejects.toThrow('useTheme must be used within a ThemeProvider');

    errRoot.remove();
  });
});

describe('ThemeProvider — initial hydration', () => {
  it('defaults to cockpit when nothing is persisted', async () => {
    await render();
    expect(latest!.themeName).toBe('cockpit');
    expect(latest!.currentTheme).toBe(cockpitTheme);
  });

  it('respects an explicit defaultTheme prop', async () => {
    await render({ defaultTheme: 'cockpit' });
    expect(latest!.themeName).toBe('cockpit');
  });

  it('hydrates a valid persisted theme from localStorage', async () => {
    localStorage.setItem('gameTheme', 'cockpit');
    await render();
    expect(latest!.themeName).toBe('cockpit');
  });

  it('falls back to defaultTheme when the persisted value is not a registered theme', async () => {
    localStorage.setItem('gameTheme', 'nonexistent-theme');
    await render();
    expect(latest!.themeName).toBe('cockpit');
  });

  it('exposes exactly the registered themes via availableThemes', async () => {
    await render();
    expect(latest!.availableThemes).toEqual([cockpitTheme]);
  });

  it('exposes colors/fonts directly via useThemeColors/useThemeFonts', async () => {
    await render();
    expect(latestColors).toBe(cockpitTheme.colors);
    expect(latestFonts).toBe(cockpitTheme.fonts);
  });
});

describe('ThemeProvider — CSS/body application', () => {
  it('applies every theme cssVariable onto document.documentElement', async () => {
    await render();
    for (const [prop, value] of Object.entries(cockpitTheme.cssVariables)) {
      expect(document.documentElement.style.getPropertyValue(prop)).toBe(value);
    }
  });

  it('adds a theme-<name> class to document.body', async () => {
    await render();
    expect(document.body.classList.contains('theme-cockpit')).toBe(true);
  });

  it('removes stale theme-* classes before applying the current one', async () => {
    document.body.classList.add('theme-stale-leftover');
    await render();
    expect(document.body.classList.contains('theme-stale-leftover')).toBe(false);
    expect(document.body.classList.contains('theme-cockpit')).toBe(true);
  });

  it('persists the theme name to localStorage on apply', async () => {
    await render();
    expect(localStorage.getItem('gameTheme')).toBe('cockpit');
  });
});

describe('ThemeProvider — setTheme', () => {
  it('is a no-op for an unregistered theme name', async () => {
    await render();
    await act(async () => {
      latest!.setTheme('nonexistent-theme' as ThemeName);
    });
    expect(latest!.themeName).toBe('cockpit');
  });

  it('accepts a registered theme name (idempotent with the only registered theme)', async () => {
    await render();
    await act(async () => {
      latest!.setTheme('cockpit');
    });
    expect(latest!.themeName).toBe('cockpit');
    expect(latest!.currentTheme).toBe(cockpitTheme);
  });
});
