// @vitest-environment jsdom
/**
 * LandingPage — live-mount smoke (WO-LANDING-NEON sub-part a+c).
 *
 * Mirrors StatusBar.smoke.test.tsx's harness (jsdom + react-dom/client
 * createRoot + act(), no RTL in this project — see that file's doc comment
 * for why). Proves the static scaffold mounts error-free, every section
 * heading and the Pumpkin easter egg are present, and the two auth
 * callbacks wired from App.tsx (onLogin / onRegister) actually fire on
 * click — the auth-carve-out boundary this component sits behind.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import LandingPage from '../LandingPage';

// Same jsdom+createRoot+act harness quirk noted in StatusBar.smoke.test.tsx
// ("current testing environment is not configured to support act(...)") --
// baseline-wide in this repo's jsdom+createRoot+act tests, unrelated to
// LandingPage itself.
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('LandingPage — live-mount smoke', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;
  let onLogin: ReturnType<typeof vi.fn>;
  let onRegister: ReturnType<typeof vi.fn>;

  beforeEach(() => {
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
});
