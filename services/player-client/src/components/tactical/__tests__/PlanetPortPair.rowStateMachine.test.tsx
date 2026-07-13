// @vitest-environment jsdom
/**
 * PlanetPortPair — SOLAR SYSTEM page row state machine (WO-UI2-WINDSHIELD-
 * TABLEAU item 3, cockpit-redesign-v10 §05 L1349-1352): each body row's
 * action mirrors the demo's monSys() exactly —
 *   here ? DOCK/LAND/CLAIM ▸ : (flying ? 🛑 HALT ▸ [.act.armed] : APPROACH ▸)
 * "here"/"flying" are passed in as props (GameDashboard computes them —
 * here = per-body id match against playerState.current_planet_id/
 * current_port_id; flying = autopilot.status === 'engaged'). This file
 * proves PlanetPortPair's own row-label + onClick wiring in isolation, no
 * GameDashboard/AutopilotContext harness needed.
 *
 * Mirrors the repo's dominant test seam (SolarSalvagePage.test.tsx et al.):
 * jsdom + react-dom/client createRoot + act(), no RTL.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import PlanetPortPair from '../PlanetPortPair';

const PLANET = {
  id: 'planet-1',
  name: 'Kepler-7',
  type: 'terran',
  status: 'active',
  sector_id: 100,
  owner_id: 'player-1',
  owner_name: 'CDR. Vega',
  population: 4200,
  habitability_score: 82,
};

const UNCLAIMED_PLANET = {
  ...PLANET,
  id: 'planet-2',
  owner_id: null,
  owner_name: null,
};

const STATION = {
  id: 'station-1',
  name: 'Kepler Ring',
  type: 'trading_post',
  status: 'operational',
  owner_id: null,
  owner_name: null,
};

describe('PlanetPortPair row state machine', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
    });
  };

  const render = async (el: React.ReactElement) => {
    await act(async () => {
      root.render(el);
    });
    await flush();
  };

  const click = async (el: Element) => {
    await act(async () => {
      el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();
  };

  const actButton = (root: HTMLElement, selector: string) =>
    root.querySelector(selector)?.querySelector('button.act') as HTMLButtonElement | null;

  // ---- Planet rows ---------------------------------------------------

  it('not here, not flying: planet row shows APPROACH ▸', async () => {
    const onLand = vi.fn();
    const onApproach = vi.fn();
    await render(
      <PlanetPortPair
        planet={PLANET}
        station={null}
        onLandOnPlanet={onLand}
        isLanded={false}
        flying={false}
        onApproach={onApproach}
      />
    );
    const btn = actButton(container, '.planet-section');
    expect(btn?.textContent).toBe('🧭 APPROACH ▸');
    expect(btn?.className).toBe('act'); // never .armed off-burn
    await click(btn!);
    // APPROACH opens the in-fiction confirm dialog (portaled to document.body,
    // not `container` — ConfirmDialog uses createPortal), doesn't call onLand yet
    expect(onLand).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain('Land on Kepler-7?');
    // WO-UI2-FLIGHT-FEEL: ALSO kicks off the windshield ship-glide, in
    // addition to (not instead of) the confirm dialog above — the row's
    // dispatch previously never reached the glide at all.
    expect(onApproach).toHaveBeenCalledTimes(1);
    expect(onApproach).toHaveBeenCalledWith('planet-1');
  });

  it('APPROACH is a no-op tolerant of a missing onApproach prop (optional, back-compat)', async () => {
    await render(
      <PlanetPortPair
        planet={PLANET}
        station={null}
        onLandOnPlanet={vi.fn()}
        isLanded={false}
        flying={false}
      />
    );
    const btn = actButton(container, '.planet-section');
    await expect(click(btn!)).resolves.toBeUndefined(); // does not throw
    expect(document.body.textContent).toContain('Land on Kepler-7?');
  });

  it('unclaimed planet, not flying: row shows CLAIM ▸ (not APPROACH), and CLAIM never fires onApproach', async () => {
    const onClaim = vi.fn();
    const onApproach = vi.fn();
    await render(
      <PlanetPortPair
        planet={UNCLAIMED_PLANET}
        station={null}
        onLandOnPlanet={vi.fn()}
        onClaimPlanet={onClaim}
        isLanded={false}
        flying={false}
        onApproach={onApproach}
      />
    );
    const btn = actButton(container, '.planet-section');
    expect(btn?.textContent).toBe('🚩 CLAIM ▸');
    await click(btn!);
    expect(document.body.textContent).toContain('Claim Kepler-7?');
    expect(onApproach).not.toHaveBeenCalled();
  });

  it('here (isLanded true), not flying: planet row shows LAND ▸', async () => {
    await render(
      <PlanetPortPair
        planet={PLANET}
        station={null}
        onLandOnPlanet={vi.fn()}
        isLanded
        flying={false}
      />
    );
    const btn = actButton(container, '.planet-section');
    expect(btn?.textContent).toBe('🛬 LAND ▸');
  });

  it('flying: planet row shows 🛑 HALT ▸ (.act.armed) regardless of here-state, and calls onHalt — never onLandOnPlanet or onApproach', async () => {
    const onLand = vi.fn();
    const onHalt = vi.fn();
    const onApproach = vi.fn();
    await render(
      <PlanetPortPair
        planet={PLANET}
        station={null}
        onLandOnPlanet={onLand}
        isLanded={false}
        flying
        onHalt={onHalt}
        onApproach={onApproach}
      />
    );
    const btn = actButton(container, '.planet-section');
    expect(btn?.textContent).toBe('🛑 HALT ▸');
    expect(btn?.className).toBe('act armed');
    await click(btn!);
    expect(onHalt).toHaveBeenCalledTimes(1);
    expect(onLand).not.toHaveBeenCalled();
    expect(onApproach).not.toHaveBeenCalled();
    // The whole section is inert while flying — clicking the card itself
    // (not the HALT button) must not open a confirm dialog either.
    const section = container.querySelector('.planet-section')!;
    expect(section.className).toContain('inactive');
    await click(section);
    expect(document.body.textContent).not.toContain('Land on Kepler-7?');
  });

  // ---- Station rows ---------------------------------------------------

  it('not here, not flying: station row shows APPROACH ▸; clicking opens the DOCK confirm AND kicks off the glide', async () => {
    const onDock = vi.fn();
    const onApproach = vi.fn();
    await render(
      <PlanetPortPair
        planet={null}
        station={STATION}
        onLandOnPlanet={vi.fn()}
        onDockAtStation={onDock}
        isDocked={false}
        flying={false}
        onApproach={onApproach}
      />
    );
    const btn = actButton(container, '.station-section');
    expect(btn?.textContent).toBe('🧭 APPROACH ▸');
    await click(btn!);
    expect(document.body.textContent).toContain('Dock at Kepler Ring?');
    expect(onDock).not.toHaveBeenCalled();
    expect(onApproach).toHaveBeenCalledTimes(1);
    expect(onApproach).toHaveBeenCalledWith('station-1');
  });

  it('here (isDocked true), not flying: station row shows DOCK ▸', async () => {
    await render(
      <PlanetPortPair
        planet={null}
        station={STATION}
        onLandOnPlanet={vi.fn()}
        onDockAtStation={vi.fn()}
        isDocked
        flying={false}
      />
    );
    const btn = actButton(container, '.station-section');
    expect(btn?.textContent).toBe('⚓ DOCK ▸');
  });

  it('flying: station row shows 🛑 HALT ▸ (.act.armed) and calls onHalt, never onDockAtStation', async () => {
    const onDock = vi.fn();
    const onHalt = vi.fn();
    await render(
      <PlanetPortPair
        planet={null}
        station={STATION}
        onLandOnPlanet={vi.fn()}
        onDockAtStation={onDock}
        isDocked={false}
        flying
        onHalt={onHalt}
      />
    );
    const btn = actButton(container, '.station-section');
    expect(btn?.textContent).toBe('🛑 HALT ▸');
    expect(btn?.className).toBe('act armed');
    await click(btn!);
    expect(onHalt).toHaveBeenCalledTimes(1);
    expect(onDock).not.toHaveBeenCalled();
  });

  it('non-operational station, not flying: no action button rendered (nothing to approach)', async () => {
    await render(
      <PlanetPortPair
        planet={null}
        station={{ ...STATION, status: 'destroyed' }}
        onLandOnPlanet={vi.fn()}
        onDockAtStation={vi.fn()}
        isDocked={false}
        flying={false}
      />
    );
    expect(actButton(container, '.station-section')).toBeNull();
  });

  it('non-operational station, flying: still shows 🛑 HALT ▸ (halting is always available)', async () => {
    await render(
      <PlanetPortPair
        planet={null}
        station={{ ...STATION, status: 'destroyed' }}
        onLandOnPlanet={vi.fn()}
        onDockAtStation={vi.fn()}
        isDocked={false}
        flying
        onHalt={vi.fn()}
      />
    );
    const btn = actButton(container, '.station-section');
    expect(btn?.textContent).toBe('🛑 HALT ▸');
  });
});
