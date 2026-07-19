// @vitest-environment jsdom
/**
 * LandingPage — live-mount smoke (WO-LANDING-NEON).
 *
 * Mirrors StatusBar.smoke.test.tsx's harness (jsdom + react-dom/client
 * createRoot + act(), no RTL in this project — see that file's doc comment
 * for why). Proves the page mounts error-free, every section heading and
 * the Pumpkin easter egg are present, and the two auth callbacks wired from
 * App.tsx (onLogin / onRegister) actually fire on click — the auth-carve-out
 * boundary this component sits behind.
 *
 * Canvas: jsdom has no real 2D rendering backend, so getContext('2d') logs
 * a console.error unless mocked (mirrors SolarSystemViewscreen
 * .livingWindshield.test.tsx's makeNoopCtx() convention) -- mocked globally
 * below so the real RAF draw loop (sub-part b) can actually run under test.
 *
 * Boot state: LandingPage shows a first-visit-only cold-boot overlay gated
 * by a localStorage seen-flag + prefers-reduced-motion. The top describe
 * block seeds localStorage as "already seen" in beforeEach so the original
 * content/auth-wiring assertions above stay focused on their own concern
 * (not boot state) -- unchanged from before sub-part (b) landed. The
 * "boot / ignition" describe block below manages its own localStorage state
 * per-test to exercise the fresh-boot path deliberately.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import LandingPage from '../LandingPage';

const LANDING_BOOT_SEEN_KEY = 'sw2102-landing-boot-seen';

// Same jsdom+createRoot+act harness quirk noted in StatusBar.smoke.test.tsx
// ("current testing environment is not configured to support act(...)") --
// baseline-wide in this repo's jsdom+createRoot+act tests, unrelated to
// LandingPage itself.
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// No-op CanvasRenderingContext2D -- every draw call is a black hole; the
// only property read as a VALUE (shadowBlur/shadowColor/globalAlpha etc are
// simple assignments, no special-casing needed). Mirrors
// SolarSystemViewscreen.livingWindshield.test.tsx's makeNoopCtx().
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

describe('LandingPage — live-mount smoke', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;
  let onLogin: ReturnType<typeof vi.fn>;
  let onRegister: ReturnType<typeof vi.fn>;
  let getContextSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    getContextSpy = vi
      .spyOn(HTMLCanvasElement.prototype, 'getContext')
      .mockImplementation((() => makeNoopCtx()) as unknown as typeof HTMLCanvasElement.prototype.getContext);
    // Not reduced-motion + already-seen -- content/auth tests below aren't
    // about boot state, so start every one of them past it (matches the
    // "returning visitor" path).
    setMatchMedia(false);
    window.localStorage.setItem(LANDING_BOOT_SEEN_KEY, '1');

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    onLogin = vi.fn();
    onRegister = vi.fn();
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    errorSpy.mockRestore();
    getContextSpy.mockRestore();
    window.localStorage.clear();
  });

  const renderLanding = async () => {
    await act(async () => {
      root.render(<LandingPage onLogin={onLogin} onRegister={onRegister} />);
    });
  };

  it('mounts with zero console errors and renders the hero + every section heading', async () => {
    await renderLanding();

    expect(container.querySelector('.landing-root')).not.toBeNull();

    const headings = Array.from(container.querySelectorAll('h1, h2')).map((h) => h.textContent);
    expect(headings).toContain('COMMAND THEGALAXY');
    expect(headings).toContain('One more jump. Then one more.');
    expect(headings).toContain('Not another space game. A universe with a mind.');
    expect(headings).toContain('Everything here is yours to seize.');
    expect(headings).toContain('There are no quests. Only consequences.');
    expect(headings).toContain("You've felt this pull before.");
    expect(headings).toContain('Your sector is waiting, Captain.');

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('renders the Pumpkin easter egg verbatim', async () => {
    await renderLanding();

    const egg = container.querySelector('.landing-easter-egg');
    expect(egg).not.toBeNull();
    expect(egg?.textContent).toContain(
      "word is there's an old orange cat who's prowled the Callisto shipyard longer than anyone can explain. Be kind on your first login; it tends to pay off."
    );

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('fires onLogin when the topbar Login button is clicked', async () => {
    await renderLanding();

    const loginBtn = container.querySelector('.landing-btn:not(.landing-btn-primary)') as HTMLButtonElement;
    expect(loginBtn?.textContent).toBe('Login');

    await act(async () => {
      loginBtn.click();
    });

    expect(onLogin).toHaveBeenCalledTimes(1);
    expect(onRegister).not.toHaveBeenCalled();
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('fires onRegister when the topbar Join Now button is clicked', async () => {
    await renderLanding();

    const joinBtn = container.querySelector('.landing-btn-primary') as HTMLButtonElement;
    expect(joinBtn?.textContent).toBe('Join Now');

    await act(async () => {
      joinBtn.click();
    });

    expect(onRegister).toHaveBeenCalledTimes(1);
    expect(onLogin).not.toHaveBeenCalled();
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('fires onRegister when the hero "HOLD TO JUMP IN" CTA is clicked', async () => {
    await renderLanding();

    const warpButtons = Array.from(container.querySelectorAll('.landing-warp')) as HTMLButtonElement[];
    const heroWarp = warpButtons.find((b) => b.textContent?.includes('HOLD TO JUMP IN'));
    expect(heroWarp).toBeDefined();

    await act(async () => {
      heroWarp!.click();
    });

    expect(onRegister).toHaveBeenCalledTimes(1);
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('fires onRegister when the final "HOLD TO ENLIST" CTA is clicked', async () => {
    await renderLanding();

    const warpButtons = Array.from(container.querySelectorAll('.landing-warp')) as HTMLButtonElement[];
    const finalWarp = warpButtons.find((b) => b.textContent?.includes('HOLD TO ENLIST'));
    expect(finalWarp).toBeDefined();

    await act(async () => {
      finalWarp!.click();
    });

    expect(onRegister).toHaveBeenCalledTimes(1);
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('renders all 16 data-driven system/command/emergent cards plus the ARIA feature card', async () => {
    await renderLanding();

    // 1 ARIA feature card + 5 signature systems + 6 command + 5 emergent = 17
    const cards = container.querySelectorAll('.landing-card');
    expect(cards.length).toBe(17);
    expect(container.querySelector('.landing-card-feature')).not.toBeNull();

    expect(errorSpy).not.toHaveBeenCalled();
  });

  // Pixel a11y gate (WO-LANDING-NEON scaffold FIX 1/2/3/4/5) -- exactly one
  // nav/main/footer landmark (no nested/duplicate <main>, the App.tsx-side
  // half of FIX 2 that made this possible), and every card/stat/comparison
  // collection uses real list/description-list semantics, not div-soup.
  it('exposes nav/main/footer landmarks and ul/li/dl list semantics', async () => {
    await renderLanding();

    expect(container.querySelectorAll('nav').length).toBe(1);
    expect(container.querySelectorAll('main').length).toBe(1);
    expect(container.querySelectorAll('footer').length).toBe(1);
    expect(container.querySelector('nav.landing-topbar')).not.toBeNull();
    // the hero/sections/easter-egg all live inside the single <main>
    expect(container.querySelector('main .landing-hero')).not.toBeNull();
    expect(container.querySelector('main .landing-easter-egg')).not.toBeNull();

    // core loop: <ul aria-label> of 5 <li>
    const loopList = container.querySelector('ul.landing-loops');
    expect(loopList?.getAttribute('aria-label')).toBe('Core loop steps');
    expect(loopList?.querySelectorAll(':scope > li.landing-loop').length).toBe(5);

    // three .landing-feat card collections, each a <ul> of <li.landing-card>
    const featLists = Array.from(container.querySelectorAll('ul.landing-feat'));
    expect(featLists.map((ul) => ul.getAttribute('aria-label'))).toEqual([
      'Signature systems',
      'What you command',
      'Emergent systems',
    ]);
    expect(featLists[0].querySelectorAll(':scope > li.landing-card').length).toBe(6); // ARIA feature + 5
    expect(featLists[1].querySelectorAll(':scope > li.landing-card').length).toBe(6);
    expect(featLists[2].querySelectorAll(':scope > li.landing-card').length).toBe(5);

    // stats band: <dl> of 4 <dt>/<dd> pairs
    const statsDl = container.querySelector('dl.landing-band-row');
    expect(statsDl).not.toBeNull();
    expect(statsDl?.querySelectorAll('dt.landing-met-l').length).toBe(4);
    expect(statsDl?.querySelectorAll('dd.landing-met-n').length).toBe(4);

    // REBORN then/now: two parallel <ul aria-label> lists, 4 <li> each,
    // decorative marker spans are aria-hidden
    const tnLists = Array.from(container.querySelectorAll('ul.landing-tnl-list'));
    expect(tnLists.map((ul) => ul.getAttribute('aria-label'))).toEqual(['THEN', 'NOW 2102']);
    tnLists.forEach((ul) => {
      expect(ul.querySelectorAll(':scope > li.landing-tnl').length).toBe(4);
    });
    expect(container.querySelectorAll('.landing-tnl-m[aria-hidden="true"]').length).toBe(8);

    expect(errorSpy).not.toHaveBeenCalled();
  });

  // Pixel gate FIX 3 -- the ARIA live-feed cycles a new line every 2.6s;
  // without aria-live a screen-reader user gets total silence. Must be
  // "polite" (an "assertive" region would interrupt on every cycle).
  it('the ARIA live feed is an aria-live="polite" region, not assertive', async () => {
    await renderLanding();

    const feed = container.querySelector('.landing-mfd-body');
    expect(feed?.getAttribute('aria-live')).toBe('polite');
    expect(feed?.getAttribute('aria-label')).toBe('Game feed');

    expect(errorSpy).not.toHaveBeenCalled();
  });

  // ---------------------------------------------------------------------
  // Hold-to-charge CTA a11y (WO-LANDING-NEON sub-part b) -- the warp
  // buttons' onClick is the single activation path shared by a full mouse
  // hold-and-release AND keyboard Enter/Space (native <button> semantics
  // dispatch the same 'click' event for both -- see LandingPage.tsx's
  // file-header note). Proven two ways: (1) a real mousedown->mouseup->
  // click sequence (the "completed a hold" path) still lands on onRegister
  // exactly once with no double-fire from the cosmetic charge handlers;
  // (2) the element is a native <button> (not a div+onClick), which is what
  // guarantees browsers dispatch 'click' for Enter/Space -- jsdom does not
  // simulate that UA default action, so this is the honest proof available
  // without a real browser (the orchestrator's browser-prove covers the
  // literal keypress).
  // ---------------------------------------------------------------------
  it('hold-to-charge: a full mousedown->mouseup->click cycle on the warp CTA fires onRegister exactly once (no double-fire from the cosmetic charge handlers)', async () => {
    await renderLanding();

    const warpButtons = Array.from(container.querySelectorAll('.landing-warp')) as HTMLButtonElement[];
    const heroWarp = warpButtons.find((b) => b.textContent?.includes('HOLD TO JUMP IN'))!;
    expect(heroWarp.tagName).toBe('BUTTON');

    await act(async () => {
      heroWarp.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      heroWarp.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
      heroWarp.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    expect(onRegister).toHaveBeenCalledTimes(1);
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('hold-to-charge: releasing the mouse outside the button (mouseleave, no click) does NOT register -- only cancels the cosmetic fill', async () => {
    await renderLanding();

    const warpButtons = Array.from(container.querySelectorAll('.landing-warp')) as HTMLButtonElement[];
    const heroWarp = warpButtons.find((b) => b.textContent?.includes('HOLD TO JUMP IN'))!;

    await act(async () => {
      heroWarp.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      heroWarp.dispatchEvent(new MouseEvent('mouseleave', { bubbles: true }));
    });

    expect(onRegister).not.toHaveBeenCalled();
    expect(errorSpy).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Boot / ignition (WO-LANDING-NEON sub-part b) -- manages its own
// localStorage + matchMedia state per-test to exercise the fresh-boot,
// reduced-motion, and boot-once paths deliberately.
//
// requestAnimationFrame/cancelAnimationFrame are mocked with a deterministic
// fake (never actually invokes the callback) rather than a pass-through spy:
// a pass-through would let the canvas's real recursive draw() loop keep
// re-scheduling itself via jsdom's real (~16ms setTimeout-backed) RAF
// polyfill for as long as each test takes to reach its own unmount --
// harmless in isolation, but under a full-suite concurrent run (many test
// files + sibling agent processes competing for CPU on this shared dev
// machine) that steady drip of real background timer work was observed to
// tip marginal tests over the default 5000ms budget (reproduced once under
// full-suite load; passed clean both in isolation and paired with the
// suite's other CPU-heavy test). Deterministic mocks remove that variable
// entirely rather than just papering over it with a longer timeout.
// ---------------------------------------------------------------------------
describe('LandingPage — boot / ignition', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;
  let getContextSpy: ReturnType<typeof vi.spyOn>;
  let rafSpy: ReturnType<typeof vi.spyOn>;
  let cancelSpy: ReturnType<typeof vi.spyOn>;
  let nextRafId: number;

  beforeEach(() => {
    getContextSpy = vi
      .spyOn(HTMLCanvasElement.prototype, 'getContext')
      .mockImplementation((() => makeNoopCtx()) as unknown as typeof HTMLCanvasElement.prototype.getContext);
    nextRafId = 1;
    rafSpy = vi
      .spyOn(window, 'requestAnimationFrame')
      .mockImplementation(() => nextRafId++) as unknown as ReturnType<typeof vi.spyOn>;
    cancelSpy = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
    window.localStorage.clear();
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    if (root) {
      act(() => {
        root.unmount();
      });
    }
    container?.remove();
    errorSpy.mockRestore();
    getContextSpy.mockRestore();
    rafSpy.mockRestore();
    cancelSpy.mockRestore();
    window.localStorage.clear();
  });

  const mount = async (reducedMotion: boolean) => {
    setMatchMedia(reducedMotion);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(<LandingPage onLogin={vi.fn()} onRegister={vi.fn()} />);
    });
  };

  it('reduced-motion snaps straight to LIVE: no boot overlay, no RAF loop', async () => {
    await mount(true);

    expect(container.querySelector('.landing-boot')).toBeNull();
    expect(container.querySelector('.landing-root')).not.toBeNull();
    // The canvas draw effect bails before ever scheduling a frame under
    // reduced motion -- getContext is never even called.
    expect(getContextSpy).not.toHaveBeenCalled();
    expect(rafSpy).not.toHaveBeenCalled();
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('a first-ever, non-reduced-motion visit shows the boot overlay; the RAF canvas loop is already running (invisible) behind it', async () => {
    await mount(false);

    expect(container.querySelector('.landing-boot')).not.toBeNull();
    expect(getContextSpy).toHaveBeenCalled();
    expect(rafSpy).toHaveBeenCalled();
    expect(errorSpy).not.toHaveBeenCalled();
  });

  // Pixel gate FIX 1 -- the boot overlay is a lightweight modal: role +
  // aria-modal so AT announces it as covering the page, and initial focus
  // lands on SKIP (the escape hatch) the instant it mounts.
  it('boot overlay: exposes role="dialog"/aria-modal and moves initial focus to SKIP on mount', async () => {
    await mount(false);

    const boot = container.querySelector('.landing-boot');
    expect(boot?.getAttribute('role')).toBe('dialog');
    expect(boot?.getAttribute('aria-modal')).toBe('true');

    const skipBtn = container.querySelector('.landing-skip');
    expect(document.activeElement).toBe(skipBtn);
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('the RAF loop cancels on unmount -- no leak, no post-unmount frame work', async () => {
    await mount(false);

    expect(rafSpy).toHaveBeenCalledTimes(1);
    const scheduledId = rafSpy.mock.results[0]?.value as number;

    act(() => {
      root.unmount();
    });

    expect(cancelSpy).toHaveBeenCalledWith(scheduledId);
  });

  it('SKIP INTRO ends the boot immediately (no flash) and marks the seen-flag', async () => {
    await mount(false);

    expect(container.querySelector('.landing-boot')).not.toBeNull();
    expect(window.localStorage.getItem(LANDING_BOOT_SEEN_KEY)).toBeNull();

    const skipBtn = container.querySelector('.landing-skip') as HTMLButtonElement;
    expect(skipBtn).not.toBeNull();

    await act(async () => {
      skipBtn.click();
    });

    expect(container.querySelector('.landing-boot')).toBeNull();
    expect(container.querySelector('.landing-flash')).toBeNull();
    expect(window.localStorage.getItem(LANDING_BOOT_SEEN_KEY)).toBe('1');
    // Pixel gate FIX 1 -- focus is restored to a sensible landing element
    // (the topbar's Login CTA) once the overlay leaves the document, not
    // left dangling on the now-unmounted SKIP button.
    const loginBtn = container.querySelector('.landing-btn:not(.landing-btn-primary)');
    expect(document.activeElement).toBe(loginBtn);
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('boot shows once per browser: a second mount after the seen-flag is set skips straight to LIVE', async () => {
    await mount(false);
    const skipBtn = container.querySelector('.landing-skip') as HTMLButtonElement;
    await act(async () => {
      skipBtn.click();
    });
    expect(window.localStorage.getItem(LANDING_BOOT_SEEN_KEY)).toBe('1');

    act(() => {
      root.unmount();
    });
    container.remove();

    // Fresh mount, SAME localStorage (the seen-flag persists) -- mirrors
    // the Annunciator/SolarSystemViewscreen convention that a
    // useState+matchMedia-style initial-value gate needs a fresh root, not
    // a re-render, to observe a different starting state.
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(<LandingPage onLogin={vi.fn()} onRegister={vi.fn()} />);
    });

    expect(container.querySelector('.landing-boot')).toBeNull();
    expect(errorSpy).not.toHaveBeenCalled();
  });
});
