// @vitest-environment jsdom
/**
 * LEG-3697 Soft-ORDER — LandingPage cold-start bootstrap TypeError densify.
 *
 * LandingPage boot gating uses localStorage helpers (no HTTP fetch on this
 * surface). Tests prove TypeError from storage access never reaches the DOM.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import LandingPage from '../LandingPage';

const LANDING_BOOT_SEEN_KEY = 'sw2102-landing-boot-seen';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function makeNoopCtx(): CanvasRenderingContext2D {
  const store: Record<string, unknown> = {};
  return new Proxy(store, {
    get(target, prop) {
      if (prop in target) return target[prop as string];
      return () => {};
    },
    set(target, prop, value) {
      target[prop as string] = value;
      return true;
    },
  }) as unknown as CanvasRenderingContext2D;
}

const setMatchMedia = (reducedMotion: boolean) => {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: reducedMotion,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })) as unknown as typeof window.matchMedia;
};

describe('LandingPage bootstrap UI TypeError densify (LEG-3697)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let getContextSpy: ReturnType<typeof vi.spyOn>;
  let nextRafId: number;

  beforeEach(() => {
    getContextSpy = vi
      .spyOn(HTMLCanvasElement.prototype, 'getContext')
      .mockImplementation((() => makeNoopCtx()) as unknown as typeof HTMLCanvasElement.prototype.getContext);
    nextRafId = 1;
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => nextRafId++);
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
    setMatchMedia(false);
    window.localStorage.clear();

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    getContextSpy.mockRestore();
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  const mount = async () => {
    await act(async () => {
      root.render(<LandingPage onLogin={vi.fn()} onRegister={vi.fn()} />);
    });
  };

  it('localStorage.getItem TypeError still shows boot overlay without leaking exception text', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new TypeError('localStorage is not available');
    });

    await mount();

    expect(container.querySelector('.landing-boot')).not.toBeNull();
    expect(container.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/localStorage is not available/i);
  });

  it('localStorage.setItem TypeError on SKIP dismisses boot without leaking exception text', async () => {
    await mount();

    expect(container.querySelector('.landing-boot')).not.toBeNull();

    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new TypeError('localStorage is not available');
    });

    const skipBtn = container.querySelector('.landing-skip') as HTMLButtonElement;
    await act(async () => {
      skipBtn.click();
    });

    expect(container.querySelector('.landing-boot')).toBeNull();
    expect(container.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/localStorage is not available/i);
    expect(window.localStorage.getItem(LANDING_BOOT_SEEN_KEY)).toBeNull();
  });

  it('returning visitor with throwing localStorage still renders stable hero without exception text', async () => {
    window.localStorage.setItem(LANDING_BOOT_SEEN_KEY, '1');
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation((key: string) => {
      if (key === LANDING_BOOT_SEEN_KEY) {
        throw new TypeError('localStorage is not available');
      }
      return null;
    });

    await mount();

    expect(container.querySelector('.landing-hero')).not.toBeNull();
    expect(container.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/localStorage is not available/i);
  });
});
