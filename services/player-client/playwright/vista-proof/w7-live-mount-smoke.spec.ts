/**
 * W7 live-mount smoke — the missing runtime gate.
 * vitest exercises the PIPELINE (model-gen) only; golden-captures can render a
 * stale vite-HMR module. This mounts the ACTUAL <VistaCanvas> engine per type via
 * the parity route (citadel-bearing adapter input → exercises the river-render
 * path) and asserts ZERO real render throws. A thrown render blanks the windshield.
 *
 * NOTE: the Mac has no backend (gameserver), so i18n/network 502s are expected
 * noise and are filtered out — we flag only genuine JS render exceptions.
 */
import { test, expect } from '@playwright/test';

const TYPES = ['TERRAN','OCEANIC','TROPICAL','MOUNTAINOUS','ARCTIC','VOLCANIC',
               'BARREN','DESERT','JUNGLE','ICE','ARTIFICIAL','GAS_GIANT'] as const;

// A "render throw" = an uncaught JS exception (pageerror) OR a console.error that
// looks like a real code fault — NOT a network/resource/i18n/translation failure.
const NOISE = /(failed to load resource|502|bad gateway|i18n|translation|proxy error|getaddrinfo|net::|favicon)/i;
const FAULT = /(referenceerror|typeerror|rangeerror|is not defined|is not a function|cannot read|undefined is not|vistacanvas)/i;

for (const type of TYPES) {
  test(`live-mount ${type} — 0 render throws`, async ({ page }) => {
    const faults: string[] = [];
    page.on('pageerror', e => faults.push(`pageerror: ${e.message}`));
    page.on('console', m => {
      if (m.type() !== 'error') return;
      const t = m.text();
      if (NOISE.test(t) && !FAULT.test(t)) return;        // drop infra noise
      if (FAULT.test(t)) faults.push(`console.error: ${t}`);
    });

    await page.goto(`/lab/vista-parity?type=${type}`);
    await page.waitForSelector('canvas', { state: 'attached', timeout: 15000 });
    await page.waitForTimeout(1200); // let RAF render settle so a throw surfaces
    const canvasCount = await page.locator('canvas').count();

    if (faults.length) console.log(`[smoke] ${type} FAULTS:\n  ${faults.slice(0,4).join('\n  ')}`);
    console.log(`[smoke] ${type}: canvases=${canvasCount} faults=${faults.length}`);
    expect(faults, `${type}: genuine render throw during VistaCanvas mount`).toEqual([]);
    expect(canvasCount, `${type}: both canvases mounted`).toBeGreaterThanOrEqual(2);
  });
}
