/**
 * W7 Parity captures — OLD (drawLandedScene) vs ENGINE (VistaCanvas).
 *
 * For each of the 12 landed-capable planet types, navigates to
 * /lab/vista-parity?type=<TYPE>, waits for the combined parity-ready gate
 * (set by VistaParity.tsx once BOTH canvases are non-blank), then:
 *
 *   1. Reads BOTH canvases via toDataURL buffer (same immune-to-compositor
 *      approach as wave5/wave6 specs).
 *   2. Writes playwright/artifacts/w7-parity-<type>-old.png and -engine.png.
 *   3. Logs per-type metrics: {nonBlack, distinctColors, pngKB} for each side.
 *   4. Runs heuristic element-presence checks (logged only, NOT asserted):
 *        sky present   — top 30% non-black > 50%
 *        ground present— bottom 30% non-black > 30%
 *        flora hint    — green tint in ground when hab ≥ 40
 *        citadel       — notes expected skyline (visual check; can't pixel-detect)
 *        sky bodies    — bright pixel count in sky when moons > 0
 *   5. Asserts ENGINE non-blank guard: ≥ 50 distinct colors, PNG > 20 KB.
 *
 * Parity bar: NOT pixel-identity — the engine is an upgrade.
 * Flag only MISSING elements or regressions in the summary report.
 *
 * OUTPUT: playwright/artifacts/w7-parity-*.png
 *         summary table in stdout after all tests complete.
 */
import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const ARTIFACTS_DIR = path.resolve(process.cwd(), 'playwright/artifacts');
const MIN_DISTINCT_COLORS = 50;
const MIN_PNG_BYTES       = 20_000;

// ---------------------------------------------------------------------------
// Browser-context helpers (serialized by Playwright to run in Chromium)
// ---------------------------------------------------------------------------

interface SideMetrics {
  ok:             boolean;
  w:              number;
  h:              number;
  nonBlack:       number;
  distinctColors: number;
  skyPct:         number;   // fraction of top-30% pixels that are non-black
  groundPct:      number;   // fraction of bottom-30% pixels that are non-black
  greenGround:    number;   // avg(green - blue) in bottom 30%; positive = flora tint
  brightSpots:    number;   // pixel count with brightness > 180 in top 30% (moon proxy)
}

/**
 * Analyse a canvas side by testid.  Runs in the browser context.
 * NOTE: no TypeScript type annotations on internals — they're fine at compile
 * time but the runtime is vanilla JS in Chromium.
 */
function analyzeCanvasSide(testid: string): SideMetrics {
  const container = document.querySelector('[data-testid="' + testid + '"]');
  const canvas = container
    ? (container.querySelector('canvas') as HTMLCanvasElement | null)
    : null;

  if (!canvas || !canvas.width || !canvas.height) {
    return { ok: false, w: 0, h: 0, nonBlack: 0, distinctColors: 0, skyPct: 0, groundPct: 0, greenGround: 0, brightSpots: 0 };
  }

  const ctx = canvas.getContext('2d');
  if (!ctx) {
    return { ok: false, w: canvas.width, h: canvas.height, nonBlack: 0, distinctColors: 0, skyPct: 0, groundPct: 0, greenGround: 0, brightSpots: 0 };
  }

  const w = canvas.width;
  const h = canvas.height;

  // Overall sparse sample (every ~4000th pixel) — matches wave5/wave6 pattern.
  const full = ctx.getImageData(0, 0, w, h).data;
  let nonBlack = 0;
  const colorSet = new Set();
  for (let i = 0; i < full.length; i += 4 * 997) {
    const r = full[i], g = full[i + 1], b = full[i + 2];
    if (r || g || b) nonBlack++;
    colorSet.add(r + ',' + g + ',' + b);
  }

  // Sky region: top 30% of canvas height
  const skyH    = Math.floor(h * 0.30);
  const skyData = ctx.getImageData(0, 0, w, skyH).data;
  let skyNB = 0, skyPx = 0, skyBright = 0;
  for (let i = 0; i < skyData.length; i += 4 * 4) {  // every 4th pixel
    skyPx++;
    const r = skyData[i], g = skyData[i + 1], b = skyData[i + 2];
    if (r > 5 || g > 5 || b > 5) skyNB++;
    if ((r + g + b) / 3 > 180) skyBright++;
  }

  // Ground region: bottom 30% of canvas height
  const gndY    = Math.floor(h * 0.70);
  const gndData = ctx.getImageData(0, gndY, w, h - gndY).data;
  let gndNB = 0, gndPx = 0, gndGreenSum = 0;
  for (let i = 0; i < gndData.length; i += 4 * 4) {
    gndPx++;
    const r = gndData[i], g = gndData[i + 1], b = gndData[i + 2];
    if (r > 5 || g > 5 || b > 5) gndNB++;
    gndGreenSum += (g - b);  // positive = green tint, negative = blue/cool/red
  }

  return {
    ok:             true,
    w,              h,
    nonBlack,
    distinctColors: colorSet.size,
    skyPct:         skyPx  > 0 ? skyNB  / skyPx         : 0,
    groundPct:      gndPx  > 0 ? gndNB  / gndPx         : 0,
    greenGround:    gndPx  > 0 ? gndGreenSum / gndPx    : 0,
    brightSpots:    skyBright,
  };
}

/** Capture a canvas side as a PNG data URL.  Runs in the browser context. */
function captureCanvasSide(testid: string): string {
  const container = document.querySelector('[data-testid="' + testid + '"]');
  const canvas = container
    ? (container.querySelector('canvas') as HTMLCanvasElement | null)
    : null;
  return canvas ? canvas.toDataURL('image/png') : '';
}

// ---------------------------------------------------------------------------
// Parity result accumulation (for the afterAll summary report)
// ---------------------------------------------------------------------------

interface CaseResult {
  type:    string;
  hab:     number;
  citadel: number;
  moons:   number;
  old:     SideMetrics & { pngKB: number };
  engine:  SideMetrics & { pngKB: number };
  verdict: string;
  notes:   string[];
}

const RESULTS: CaseResult[] = [];

// ---------------------------------------------------------------------------
// Test cases: one per landed-capable planet type
// ---------------------------------------------------------------------------

test.beforeAll(() => { fs.mkdirSync(ARTIFACTS_DIR, { recursive: true }); });

const CASES = [
  { type: 'TERRAN',      hab: 85, citadel: 2, moons: 0 },
  { type: 'OCEANIC',     hab: 70, citadel: 1, moons: 1 },
  { type: 'TROPICAL',    hab: 74, citadel: 1, moons: 0 },
  { type: 'MOUNTAINOUS', hab: 52, citadel: 2, moons: 0 },
  { type: 'ARCTIC',      hab: 22, citadel: 1, moons: 0 },
  { type: 'VOLCANIC',    hab: 12, citadel: 1, moons: 0 },
  { type: 'BARREN',      hab:  5, citadel: 0, moons: 0 },
  { type: 'DESERT',      hab: 22, citadel: 1, moons: 0 },
  { type: 'JUNGLE',      hab: 78, citadel: 2, moons: 1 },
  { type: 'ICE',         hab: 18, citadel: 1, moons: 0 },
  { type: 'ARTIFICIAL',  hab: 62, citadel: 2, moons: 0 },
  { type: 'GAS_GIANT',   hab:  0, citadel: 0, moons: 2 },
] as const;

for (const { type, hab, citadel, moons } of CASES) {
  test(`W7 parity — ${type}`, async ({ page }) => {
    await page.goto(`/lab/vista-parity?type=${type}`);

    // Wait for the VistaParity readiness gate: both canvases non-blank.
    // Timeout 30 s covers slow CI; the gate caps at MAX_SETTLE_FRAMES and
    // resolves itself before that if the engine takes a few frames to paint.
    // The ready marker is an intentionally invisible (display:none) signal node
    // that only mounts once BOTH canvases have content — so wait for it ATTACHED,
    // not visible (a zero-size/display:none node is never "visible" to Playwright).
    await page.waitForSelector('[data-testid="parity-ready"]', { state: 'attached', timeout: 30_000 });

    // Analyse both sides
    const oldMetrics = await page.evaluate(analyzeCanvasSide, 'parity-old-container');
    const engMetrics = await page.evaluate(analyzeCanvasSide, 'parity-engine-container');

    // Capture PNGs via toDataURL (buffer-reads; immune to compositor lag)
    const oldDataUrl = await page.evaluate(captureCanvasSide, 'parity-old-container');
    const engDataUrl = await page.evaluate(captureCanvasSide, 'parity-engine-container');

    const oldBuf = oldDataUrl ? Buffer.from(oldDataUrl.split(',')[1] ?? '', 'base64') : Buffer.alloc(0);
    const engBuf = engDataUrl ? Buffer.from(engDataUrl.split(',')[1] ?? '', 'base64') : Buffer.alloc(0);

    const oldOut = path.join(ARTIFACTS_DIR, `w7-parity-${type.toLowerCase()}-old.png`);
    const engOut = path.join(ARTIFACTS_DIR, `w7-parity-${type.toLowerCase()}-engine.png`);
    if (oldBuf.length > 0) fs.writeFileSync(oldOut, oldBuf);
    if (engBuf.length > 0) fs.writeFileSync(engOut, engBuf);

    // -----------------------------------------------------------------------
    // Heuristic element-presence checks — LOGGED ONLY, not asserted.
    // The bar is not pixel-identity: flag missing elements, not visual upgrades.
    // -----------------------------------------------------------------------
    const notes: string[] = [];

    if (!oldMetrics.ok)  notes.push('WARN: old canvas not found');
    if (!engMetrics.ok)  notes.push('WARN: engine canvas not found');

    // Sky present (top 30% ≥ 50% non-black)
    if (oldMetrics.skyPct < 0.50) notes.push(`WARN[old] sky sparse (${(oldMetrics.skyPct * 100).toFixed(0)}%)`);
    if (engMetrics.skyPct < 0.50) notes.push(`WARN[eng] sky sparse (${(engMetrics.skyPct * 100).toFixed(0)}%)`);
    else                          notes.push(`OK[eng] sky ${(engMetrics.skyPct * 100).toFixed(0)}%`);

    // Horizon/ground band present (bottom 30% ≥ 30% non-black)
    if (oldMetrics.groundPct < 0.30) notes.push(`WARN[old] ground sparse (${(oldMetrics.groundPct * 100).toFixed(0)}%)`);
    if (engMetrics.groundPct < 0.30) notes.push(`WARN[eng] ground sparse (${(engMetrics.groundPct * 100).toFixed(0)}%)`);
    else                             notes.push(`OK[eng] ground ${(engMetrics.groundPct * 100).toFixed(0)}%`);

    // Flora-or-desolation appropriate to habitability
    if (hab >= 40) {
      // Lush world — expect a positive green tint in the ground region
      if (engMetrics.greenGround < 2) {
        notes.push(`WARN[eng] flora hint weak (hab=${hab}, greenGround=${engMetrics.greenGround.toFixed(1)})`);
      } else {
        notes.push(`OK[eng] flora green=${engMetrics.greenGround.toFixed(1)} (hab=${hab})`);
      }
    } else {
      // Desolate world — low green bias expected; note it
      notes.push(`OK desolation-expected (hab=${hab}, greenGround=${engMetrics.greenGround.toFixed(1)})`);
    }

    // Citadel skyline: can't pixel-detect reliably; flag for visual review
    if (citadel > 0) {
      notes.push(`citadel=${citadel} → skyline expected (visual check)`);
    }

    // Sky bodies: when moons > 0, look for bright pixels in the sky region
    if (moons > 0) {
      if (engMetrics.brightSpots < 5) {
        notes.push(`WARN[eng] moons=${moons} but few bright sky pixels (${engMetrics.brightSpots}) — may be below horizon`);
      } else {
        notes.push(`OK[eng] sky bright spots: ${engMetrics.brightSpots} (moons=${moons})`);
      }
    }

    // -----------------------------------------------------------------------
    // Verdict (for the summary report)
    // -----------------------------------------------------------------------
    let verdict = 'PASS';
    if (!engMetrics.ok || engMetrics.nonBlack === 0) {
      verdict = 'FAIL_ENGINE_BLANK';
    } else if (engMetrics.distinctColors < MIN_DISTINCT_COLORS) {
      verdict = `WARN_ENG_COLORS=${engMetrics.distinctColors}`;
    } else if (engMetrics.skyPct < 0.10 || engMetrics.groundPct < 0.10) {
      verdict = 'MISSING_ELEMENTS';
    }

    RESULTS.push({
      type, hab, citadel, moons,
      old:    { ...oldMetrics, pngKB: Math.round(oldBuf.length / 1024) },
      engine: { ...engMetrics, pngKB: Math.round(engBuf.length / 1024) },
      verdict, notes,
    });

    // Per-test log line
    console.log(
      `[w7] ${type}:` +
      ` old={c=${oldMetrics.distinctColors},${Math.round(oldBuf.length / 1024)}KB}` +
      ` engine={c=${engMetrics.distinctColors},${Math.round(engBuf.length / 1024)}KB}` +
      ` → ${verdict}`
    );
    if (notes.length) console.log('        ' + notes.join(' | '));
    if (oldBuf.length > 0) console.log(`[w7] CAPTURED: ${oldOut} (${oldBuf.length} bytes)`);
    if (engBuf.length > 0) console.log(`[w7] CAPTURED: ${engOut} (${engBuf.length} bytes)`);

    // -----------------------------------------------------------------------
    // Hard assertions — ENGINE side only (same guard as wave5/wave6 specs)
    // -----------------------------------------------------------------------
    expect(engMetrics.ok,
      `${type} engine: canvas + 2d context must be accessible`
    ).toBe(true);

    expect(engMetrics.distinctColors,
      `${type} engine: ≥${MIN_DISTINCT_COLORS} distinct colors (blank/black frame ≈ 1 color)`
    ).toBeGreaterThanOrEqual(MIN_DISTINCT_COLORS);

    expect(engBuf.length,
      `${type} engine: PNG > ${MIN_PNG_BYTES} bytes (a black frame is ~2.7 KB)`
    ).toBeGreaterThan(MIN_PNG_BYTES);
  });
}

// ---------------------------------------------------------------------------
// Parity report — printed after all type captures complete
// ---------------------------------------------------------------------------

test.afterAll(() => {
  if (RESULTS.length === 0) return;

  const SEP = '═'.repeat(92);
  const sep = '─'.repeat(92);

  console.log('\n' + SEP);
  console.log('  W7 PARITY REPORT — drawLandedScene (OLD) vs VistaCanvas ENGINE');
  console.log('  Bar: engine renders a credible match-or-better with NO MISSING elements.');
  console.log('  Review PNGs at playwright/artifacts/w7-parity-*-{old,engine}.png');
  console.log(SEP);

  console.log(
    'TYPE'.padEnd(14) +
    'VERDICT'.padEnd(26) +
    'OLD c/KB'.padEnd(14) +
    'ENG c/KB'.padEnd(14) +
    'SKYold/eng'.padEnd(14) +
    'GNDold/eng'
  );
  console.log(sep);

  for (const r of RESULTS) {
    const o = r.old, e = r.engine;
    const oldCS  = `${o.distinctColors}/${o.pngKB}K`;
    const engCS  = `${e.distinctColors}/${e.pngKB}K`;
    const skyStr = `${(o.skyPct * 100).toFixed(0)}%/${(e.skyPct * 100).toFixed(0)}%`;
    const gndStr = `${(o.groundPct * 100).toFixed(0)}%/${(e.groundPct * 100).toFixed(0)}%`;

    console.log(
      r.type.padEnd(14) +
      r.verdict.padEnd(26) +
      oldCS.padEnd(14) +
      engCS.padEnd(14) +
      skyStr.padEnd(14) +
      gndStr
    );
    if (r.notes.length) {
      console.log(' '.repeat(14) + r.notes.join(' | '));
    }
  }

  console.log(sep);
  const passes  = RESULTS.filter(r => r.verdict === 'PASS').length;
  const missing = RESULTS.filter(r => r.verdict === 'MISSING_ELEMENTS').length;
  const fails   = RESULTS.filter(r => r.verdict.startsWith('FAIL')).length;
  console.log(`  PASS: ${passes}/${RESULTS.length}  MISSING_ELEMENTS: ${missing}  FAIL: ${fails}`);
  console.log(SEP + '\n');
});
