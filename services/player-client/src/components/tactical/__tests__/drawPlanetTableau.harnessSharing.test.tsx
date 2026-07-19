// @vitest-environment jsdom
/**
 * PlanetTableauLayer — shared-harness proof (WO-AAA-SOLAR-TABLEAU phase 3).
 *
 * The design brief's whole point of ONE `tableauFxHarness` is that the sun's
 * implied light direction and the planets' terminator/rim never phase-drift
 * onto two independent rAF clocks. This proves the wiring end-to-end at the
 * `PlanetTableauLayer` level (WindshieldTableau.tsx hands it the SAME
 * `useTableauFx(sceneSpaceRef)` instance it hands `StarDisc`): given an
 * externally-supplied `harness` prop, this layer registers its canvas
 * against THAT instance and never spins up its own independent rAF loop.
 * Mount idiom matches StarDisc.test.tsx / WindshieldTableau.test.tsx's own
 * convention (react-dom/client createRoot + act).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { PlanetTableauLayer } from '../drawPlanetTableau';
import type { TableauFxDrawFn, TableauFxHarness } from '../tableauFxHarness';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function fakeHarness(): { harness: TableauFxHarness; draws: TableauFxDrawFn[] } {
  const draws: TableauFxDrawFn[] = [];
  const harness: TableauFxHarness = {
    register: vi.fn((_canvas: HTMLCanvasElement, draw: TableauFxDrawFn) => {
      draws.push(draw);
      return vi.fn();
    }),
    drawNow: vi.fn(),
    destroy: vi.fn(),
  };
  return { harness, draws };
}

const STAR = { xPct: 12, yPct: 50, sizeEm: 2.4 };

describe('PlanetTableauLayer — shared harness (WO-AAA-SOLAR-TABLEAU phase 3)', () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  let containerRef: React.RefObject<HTMLElement | null>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    containerRef = React.createRef<HTMLElement>();
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
    vi.restoreAllMocks();
  });

  it('registers exactly once against an externally-supplied harness, manageSize default (true)', async () => {
    const { harness } = fakeHarness();
    await act(async () => {
      root.render(
        <div ref={containerRef as React.RefObject<HTMLDivElement>}>
          <PlanetTableauLayer
            containerRef={containerRef}
            harness={harness}
            sectorId={77}
            bodies={[]}
            star={STAR}
            remPx={16}
          />
        </div>,
      );
    });
    expect(harness.register).toHaveBeenCalledTimes(1);
    const [canvasArg] = (harness.register as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(canvasArg).toBe(container.querySelector('canvas.planet-tableau-fx'));
  });

  it('never spins up its own independent rAF loop when a harness is supplied (ONE shared clock, not two)', async () => {
    const { harness } = fakeHarness();
    const rafSpy = vi.spyOn(globalThis, 'requestAnimationFrame');
    await act(async () => {
      root.render(
        <div ref={containerRef as React.RefObject<HTMLDivElement>}>
          <PlanetTableauLayer
            containerRef={containerRef}
            harness={harness}
            sectorId={77}
            bodies={[]}
            star={STAR}
            remPx={16}
          />
        </div>,
      );
    });
    // A standalone `useTableauFx` would call `createTableauFxHarness`, which
    // schedules its own rAF loop (`start()` -> `requestAnimationFrame`) --
    // zero calls proves no second, independent harness was ever created.
    expect(rafSpy).not.toHaveBeenCalled();
  });

  it('`harness` omitted entirely (standalone use) still self-creates its own harness off containerRef -- backward-compatible', async () => {
    const rafSpy = vi.spyOn(globalThis, 'requestAnimationFrame');
    await act(async () => {
      root.render(
        <div ref={containerRef as React.RefObject<HTMLDivElement>}>
          <PlanetTableauLayer
            containerRef={containerRef}
            sectorId={77}
            bodies={[]}
            star={STAR}
            remPx={16}
          />
        </div>,
      );
    });
    // Standalone mode DOES own an rAF loop -- this is the pre-phase-3
    // behavior, preserved for any caller that mounts this layer on its own.
    expect(rafSpy).toHaveBeenCalled();
  });
});
