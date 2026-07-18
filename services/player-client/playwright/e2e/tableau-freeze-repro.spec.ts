import { test, expect } from '@playwright/test';

/**
 * planet-tableau-freeze repro + PERMANENT regression guard (monk dispatch,
 * WO: Skia float32 rotation collapse). Drives the isolated
 * /lab/tableau-freeze-repro page (StarDisc + PlanetTableauLayer sharing ONE
 * tableauFxHarness, same asymmetric mount timing as WindshieldTableau.tsx:
 * PlanetTableauLayer mounts immediately, StarDisc mounts ~300ms later behind
 * an async `system` resolve) and asserts BOTH canvases actually animate.
 *
 * Root cause this guards: `drawPlanetSurfaceTableau`'s `spin` fed the raw
 * epoch-scale harness `t` (~1.78e9) straight into `ctx.rotate()` — Chromium's
 * Skia Canvas2D backend represents transform-matrix trig in float32
 * internally, so a real, correctly-advancing frame-to-frame `spin` delta
 * silently collapsed to the SAME float32 value and the rotation froze even
 * though the draw callback kept firing every tick. Fixed in
 * drawPlanetTableau.tsx by subtracting a mount-time `t0` before calling
 * `drawPlanetTableau(...)` — same pattern StarDisc.tsx's own `uTime` fix
 * already uses (StarDisc.tsx:204-229). See `.claude/agent-memory/monk/
 * canvas2d-skia-float32-rotation-collapse.md` for the full diagnosis.
 */

async function imageDataHash(page: import('@playwright/test').Page, selector: string): Promise<string> {
  return page.evaluate((sel) => {
    const canvas = document.querySelector(sel) as HTMLCanvasElement | null;
    if (!canvas) return 'NO_CANVAS';
    const ctx = canvas.getContext('2d');
    if (!ctx) return 'NO_2D_CTX';
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    let hash = 0;
    let nonZero = 0;
    for (let i = 0; i < data.length; i++) {
      hash = (hash * 31 + data[i]) | 0;
      if (data[i] !== 0) nonZero++;
    }
    return `${hash}:${nonZero}`;
  }, selector);
}

async function dataUrlHash(page: import('@playwright/test').Page, selector: string): Promise<string> {
  return page.evaluate((sel) => {
    const canvas = document.querySelector(sel) as HTMLCanvasElement | null;
    if (!canvas) return 'NO_CANVAS';
    try {
      return canvas.toDataURL().slice(-200);
    } catch (e) {
      return 'READ_ERROR:' + String(e);
    }
  }, selector);
}

test('planet-tableau-fx canvas animates over time (regression guard for the Skia float32 rotation collapse)', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });
  page.on('pageerror', (err) => consoleErrors.push('PAGEERROR: ' + err.message));

  await page.goto('/lab/tableau-freeze-repro');
  // Wait for the repro's own ready marker (always present) then for the
  // async `system` resolve (300ms) + StarDisc's own mount to settle.
  await page.waitForSelector('[data-testid="repro-ready"]', { state: 'attached', timeout: 15_000 });
  await page.waitForSelector('canvas.star-disc-fx', { state: 'attached', timeout: 15_000 });
  await page.waitForTimeout(1000); // let both canvases get several rAF ticks in

  const planetHash0 = await imageDataHash(page, 'canvas.planet-tableau-fx');
  const starHash0 = await dataUrlHash(page, 'canvas.star-disc-fx');

  await page.waitForTimeout(5000);

  const planetHash5 = await imageDataHash(page, 'canvas.planet-tableau-fx');
  const starHash5 = await dataUrlHash(page, 'canvas.star-disc-fx');

  const counters = await page.evaluate(() => {
    const w = window as any;
    return {
      drawAll:      w.__drawAll ?? null,
      regSize:      w.__regSize ?? null,
      regCount:     w.__regCount ?? null,
      strF:         w.__strF ?? null,
      plnF:         w.__plnF ?? null,
      strHarnessId: w.__strHarnessId ?? null,
      plnHarnessId: w.__plnHarnessId ?? null,
      plnTLog:      w.__plnTLog ?? null,
      plnTLast:     w.__plnTLast ?? null,
      plnSpinLog:   w.__plnSpinLog ?? null,
    };
  });

  console.log('COUNTERS', JSON.stringify(counters));
  console.log('PLANET_HASH_0', planetHash0);
  console.log('PLANET_HASH_5', planetHash5);
  console.log('STAR_HASH_0', starHash0);
  console.log('STAR_HASH_5', starHash5);
  console.log('CONSOLE_ERRORS', JSON.stringify(consoleErrors));

  // PERMANENT regression guard: the planet canvas must actually repaint
  // different pixels over a real ~5s window. Before the t0 fix this was
  // byte-identical (the Skia float32 rotation collapse) despite the draw
  // callback firing every tick -- this is the exact assertion that catches
  // a re-introduction of that bug (or a future raw-epoch-t regression in
  // any of drawPlanetSurfaceTableau/drawCloudDrift/drawFormingEffectTableau/
  // drawCityLights, all of which are threaded from the same `t`).
  expect(planetHash0).not.toBe(planetHash5);
  // The star must keep animating too (unrelated to this fix, but a cheap
  // sanity check that the shared harness itself is still alive).
  expect(starHash0).not.toBe(starHash5);
});
