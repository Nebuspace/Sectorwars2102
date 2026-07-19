import React, { useEffect, useRef, useState } from 'react';
import StarDisc from '../StarDisc';
import { PlanetTableauLayer } from '../drawPlanetTableau';
import { useTableauFx } from '../tableauFxHarness';
import type { SystemBody } from '../SolarSystemViewscreen';
import type { StarAnchor } from '../windshieldTableauLayout';

/**
 * TableauFreezeRepro — PERMANENT real-Chromium regression harness for the
 * mount-relative-clock rule: any tableauFxHarness-driven draw callback that
 * feeds the harness's raw epoch-scale `t` (`Date.now()/1000`, ~1.78e9
 * magnitude) into rotation/animation math will silently freeze on screen the
 * moment that value reaches a single-precision stage of the render pipeline
 * -- Chromium's Skia backend represents Canvas2D transform-matrix trig in
 * float32 internally (drawPlanetTableau.tsx's `spin`, fixed), and a GLSL
 * `float uTime` uniform is float32 by definition (StarDisc.tsx, fixed) --
 * even though the JS double driving it keeps advancing correctly underneath.
 * A double-precision unit test cannot see this collapse (see
 * drawPlanetTableau.tDependence.test.ts's own header caveat); only an actual
 * rendered canvas, read back via `getImageData`/`toDataURL` in a real
 * browser, can. `tableau-freeze-repro.spec.ts` is that guard.
 *
 * Mirrors WindshieldTableau.tsx's own harness wiring EXACTLY, including the
 * asymmetric mount timing that made the original bug hard to spot: `system`
 * starts null and resolves asynchronously (WindshieldTableau.tsx's own
 * `GET /sectors/{id}` fetch) -- `PlanetTableauLayer` renders unconditionally
 * from mount (WindshieldTableau.tsx:1164), while `StarDisc` is gated behind
 * `system?.star &&` (WindshieldTableau.tsx:1154) and so mounts LATER, after
 * the async data arrives. Under React.StrictMode this means PlanetTableau-
 * Layer lives through the harness's own double-invoked mount effect
 * (null -> H1 -> destroy -> null -> H2), while StarDisc's own (also double-
 * invoked) mount only ever sees the already-stable H2 -- reproducing the
 * real component tree's own mount order is what makes this repro faithful.
 */

const FAKE_STAR: StarAnchor = { xPct: 20, yPct: 50, sizeEm: 3 };

const FAKE_BODY: SystemBody = {
  slot: 0,
  orbit_au: 0.5,
  kind: 'TERRAN',
  size_class: 3,
  palette: { hue: 200, sat: 0.6 },
  rings: false,
  moons: 0,
  phase_deg: 0,
  real: true,
  planet_id: 'repro-1',
  name: 'Repro World',
};

function ReproInner() {
  const sceneSpaceRef = useRef<HTMLDivElement>(null);
  const fxHarness = useTableauFx(sceneSpaceRef);
  const [system, setSystem] = useState<{ star: { kind: string; color: string }; bodies: SystemBody[] } | null>(null);

  // Mirrors WindshieldTableau.tsx's own async `GET /sectors/{id}` --
  // `system` starts null and resolves after mount, well after the
  // harness's own StrictMode double-invoke dance has settled.
  useEffect(() => {
    const id = setTimeout(() => {
      setSystem({ star: { kind: 'G_TYPE', color: '#ffcc66' }, bodies: [FAKE_BODY] });
    }, 300);
    return () => clearTimeout(id);
  }, []);

  return (
    <div className="ssv-tableau" style={{ width: 800, height: 600, position: 'relative', background: '#000' }}>
      <div ref={sceneSpaceRef} className="scene space" style={{ position: 'absolute', inset: 0 }}>
        {system?.star && (
          <StarDisc
            className="star-disc-fx"
            harness={fxHarness}
            star={FAKE_STAR}
            kind={system.star.kind}
            color={system.star.color}
            remPx={16}
          />
        )}
        <PlanetTableauLayer
          harness={fxHarness}
          containerRef={sceneSpaceRef}
          sectorId={1}
          bodies={system?.bodies ?? []}
          star={FAKE_STAR}
          remPx={16}
        />
      </div>
      <div data-testid="repro-ready" style={{ display: 'none' }}>ready</div>
    </div>
  );
}

export default function TableauFreezeRepro() {
  return (
    <React.StrictMode>
      <ReproInner />
    </React.StrictMode>
  );
}
