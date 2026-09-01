// @vitest-environment jsdom
/**
 * LEG-3697 Soft-ORDER — LandingPage cold-start bootstrap TypeError densify.
 *
 * LandingPage has no HTTP fetch/bootstrap paths; session bootstrap is the
 * localStorage seen-flag + cold-start boot overlay (hasSeenLandingBootSync /
 * markLandingBootSeen catch blocks).
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

function installThrowingLocalStorage() {
  const storage = {
    getItem: vi.fn(() => {
      throw new TypeError('localStorage is not available');
    }),
    setItem: vi.fn(() => {
      throw new TypeError('localStorage is not available');
    }),
    removeItem: vi.fn(() => {
      throw new TypeError('localStorage is not available');
    }),
    clear: vi.fn(() => {
      throw new TypeError('localStorage is not available');
    }),
    key: vi.fn(() => {
      throw new TypeError('localStorage is not available');
    }),
    length: 0,
  };
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: storage,
  });
  return storage;
}

describe('LandingPage TypeError densify (LEG-3697)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let getContextSpy: ReturnType<typeof vi.spyOn>;
  let onLogin: ReturnType<typeof vi.fn<() => void>>;
  let onRegister: ReturnType<typeof vi.fn<() => void>>;

  beforeEach(() => {
    getContextSpy = vi
      .spyOn(HTMLCanvasElement.prototype, 'getContext')
      .mockImplementation((() => makeNoopCtx()) as unknown as typeof HTMLCanvasElement.prototype.getContext);
    setMatchMedia(false);
    onLogin = vi.fn();
    onRegister = vi.fn();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    getContextSpy.mockRestore();
    vi.unstubAllGlobals();
  });

  const renderLanding = async () => {
    await act(async () => {
      root.render(<LandingPage onLogin={onLogin} onRegister={onRegister} />);
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  };

  it('first-visit boot survives localStorage TypeError without leaking exception text', async () => {
    installThrowingLocalStorage();

    await renderLanding();

    expect(container.querySelector('.landing-boot')).not.toBeNull();
    expect(container.querySelector('.landing-skip')).not.toBeNull();
    expect(document.body.textContent).not.toMatch(/localStorage is not available/i);
    expect(document.body.textContent).not.toMatch(/TypeError/i);
  });

  it('returning-visitor path survives localStorage TypeError with stable hero content', async () => {
    installThrowingLocalStorage();

    await renderLanding();

    expect(container.querySelector('.landing-root')).not.toBeNull();
    expect(container.querySelector('.landing-hero')).not.toBeNull();
    expect(document.body.textContent).not.toMatch(/localStorage is not available/i);
    expect(document.body.textContent).not.toMatch(/TypeError/i);
  });

  it('SKIP INTRO on boot overlay does not surface raw localStorage errors', async () => {
    installThrowingLocalStorage();

    await renderLanding();

    const skip = container.querySelector('.landing-skip') as HTMLButtonElement;
    expect(skip).toBeTruthy();

    await act(async () => {
      skip.click();
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(container.querySelector('.landing-boot')).toBeNull();
    expect(document.body.textContent).not.toMatch(/localStorage is not available/i);
    expect(document.body.textContent).not.toMatch(/TypeError/i);
  });
});
