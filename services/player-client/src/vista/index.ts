/**
 * Vista Engine — public barrel
 *
 * Exports createVistaEngine() and all contract types.
 *
 * generate()  → Lane B (core/pipeline.ts); pure + deterministic, no DOM.
 * mount()     → Lane C (render/canvas2d/backend.ts); canvas2d renderer.
 *
 * The "cannot find module './core/pipeline'" tsc error is expected until
 * Lane B lands at integrate time — it is listed as EXPECTED-and-OK.
 */

import { generateVista } from './core/pipeline';
import { PROFILED_TYPES } from './core/profiles';
import { mount } from './render/canvas2d/backend';
import { VISTA_CONTRACT_VERSION } from './contract';
import type { VistaEngine, PlanetType } from './contract';

// Re-export every contract symbol so consumers only need to import from 'vista'
export * from './contract';

/**
 * Instantiate the vista engine.  Returns a VistaEngine whose generate() is
 * the deterministic pipeline (Lane B) and whose mount() is the canvas2d
 * backend (Lane C).
 *
 * Usage:
 *   const engine = createVistaEngine();
 *   const model  = engine.generate(input);
 *   const handle = engine.mount(model, { canvas, backend: 'canvas2d' });
 *   // on every animation frame:
 *   handle.setTime(elapsed);
 *   // on unmount:
 *   handle.dispose();
 */
export function createVistaEngine(): VistaEngine {
  return {
    contractVersion: VISTA_CONTRACT_VERSION,
    generate: generateVista,
    mount,
    describe(): { types: PlanetType[]; backends: ('canvas2d' | 'webgl')[] } {
      return {
        types: PROFILED_TYPES,
        backends: ['canvas2d'],
      };
    },
  };
}
