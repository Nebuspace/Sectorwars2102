// WO-SOLARUI computed-layout proof (headless chromium via Playwright).
// Loads the REAL stylesheets and mounts the EXACT DOM anatomy from
// GameDashboard.tsx's SOLAR SYSTEM monitor, then measures getBoundingClientRect
// to assert the WO's falsifiable ratios:
//   (1) header toggle height <= ~1.6x .mtitle cap-height AND <= header height
//   (2) a planet .planet-section row height within ~1.3x a normal star .row
//   (3) no regression: station row stays symmetric with planet row
// Run: node .neon-proof/solarui_prove.mjs
import { chromium } from '@playwright/test';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const PC = path.resolve('services/player-client/src/components');
// Chromium blocks file:// subresource loads from a setContent (data-origin)
// page, so we INLINE each real stylesheet into <style> blocks instead — same
// bytes, same cascade order, but no cross-origin subresource fetch. Order
// mirrors GameDashboard's import graph: shell + cockpit first, then the
// tactical files (so planet-port-pair's scoped rules + solar's header rule
// land AFTER the bare cockpit.css rules they override — exactly as production).
const readCss = (rel) => readFileSync(path.join(PC, rel), 'utf8');
const inlined = [
  'layouts/cockpit-shell.css',
  'pages/cockpit.css',
  'tactical/planet-port-pair.css',
  'tactical/solar-system-viewscreen.css',
].map((r) => `<style data-src="${r}">\n${readCss(r)}\n</style>`).join('\n');

const html = `<!doctype html><html><head>
${inlined}
<style>html,body{margin:0;background:#04070C;}
  /* Give the SOLAR monitor a realistic ~430px center-column width (its real
     share of a 1440 cockpit deck.flight 30/1fr/29 grid) so rows lay out on one
     line as in production, instead of a squeezed test column that would force
     artificial vertical wrapping. Structure/classes untouched. */
  .proof-col { width: 430px; }
</style>
</head><body>
<div class="stage" data-mode="space">
  <div class="lower"><div class="deck single proof-col">
    <div class="mon system-monitor">
      <div class="mhead">
        <span class="mtitle">SOLAR SYSTEM</span>
        <span class="hsub">SECTOR ALPHA</span>
        <button type="button" class="act system-filter-toggle" aria-pressed="false"
          aria-label="Hide uninhabitable bodies">🌑 HIDE UNINHABITABLE</button>
      </div>
      <div class="mbody" role="tabpanel">
        <!-- a normal single-line star .row (the density baseline) -->
        <div class="row" id="starRow"><b>★ SOL (G2V STAR)</b><span class="dim">CORE</span></div>
        <!-- EXACT PlanetPortPair.tsx DOM: icon+name+quals ALL inside
             .planet-info (line 265-275). Long qualifier list forces the
             .planet-quals wrap to line 2 in a narrow panel, matching a real
             claimed habitable world (Max's "two lines is correct" case). -->
        <div class="planet-port-pair">
          <div class="planet-section clickable" id="planetSection">
            <div class="planet-info">
              <span class="planet-icon">🌍</span>
              <span class="planet-name">NEW TERRA PRIME</span>
              <span class="planet-quals">
                <span class="pq-owner">CLAIMED</span>
                <span class="pq-status">HABITABLE</span>
                <span class="pq-stat">🌡️72%</span>
                <span class="pq-stat">👥4.2M</span>
              </span>
            </div>
            <button class="act">🧭 APPROACH ▸</button>
          </div>
        </div>
        <!-- a station row (symmetry check) -->
        <div class="planet-port-pair">
          <div class="station-section clickable" id="stationSection">
            <div class="station-info">
              <span class="station-icon">🛰️</span>
              <span class="station-name">TRADE HUB CAPELWORKS</span>
              <span class="station-status">🟢</span>
              <span class="planet-quals"><span class="pq-status">CLASS 7 · TRADE HUB</span></span>
            </div>
            <button class="act">🧭 APPROACH ▸</button>
          </div>
        </div>
      </div>
    </div>
  </div></div>
</div>
</body></html>`;

const H = (sel) => sel; // readability

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.setContent(html, { waitUntil: 'networkidle' });
// let fonts/emoji settle
await page.evaluate(() => document.fonts && document.fonts.ready);

// GUARD: prove the real CSS actually applied before trusting any measurement.
// (An earlier file:// version silently loaded ZERO css and every default-value
//  measurement coincidentally "passed" — never again.) `.row` MUST carry
// cockpit-shell.css's `border-bottom:1px dashed`; if it's 0, the sheets didn't
// apply and the whole proof is void.
const cssApplied = await page.evaluate(() => {
  const row = document.querySelector('#starRow');
  const cs = getComputedStyle(row);
  const mb = getComputedStyle(document.querySelector('.system-monitor .mbody'));
  return { rowBorder: cs.borderBottomStyle + ' ' + cs.borderBottomWidth, mbodyFs: mb.fontSize };
});
if (!cssApplied.rowBorder.startsWith('dashed') || cssApplied.rowBorder.endsWith('0px')) {
  console.error('ABORT: stylesheets did not apply (.row border =', cssApplied.rowBorder,
    '/ .mbody fs =', cssApplied.mbodyFs, ') — proof void.');
  await browser.close();
  process.exit(2);
}
console.log('CSS-applied guard OK: .row border =', cssApplied.rowBorder, '| .mbody fs =', cssApplied.mbodyFs);

const m = await page.evaluate(() => {
  const rect = (s) => { const el = document.querySelector(s); if (!el) return null;
    const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return { h: +r.height.toFixed(2), w: +r.width.toFixed(2), fs: cs.fontSize,
             bg: cs.backgroundImage, border: cs.borderTopWidth + ' ' + cs.borderTopStyle }; };
  return {
    mhead: rect('.system-monitor .mhead'),
    mtitle: rect('.system-monitor .mhead .mtitle'),
    toggle: rect('.system-monitor .mhead .system-filter-toggle'),
    starRow: rect('#starRow'),
    planetSection: rect('#planetSection'),
    stationSection: rect('#stationSection'),
    planetIcon: rect('#planetSection .planet-icon'),
    planetName: rect('#planetSection .planet-name'),
    // per-part height breakdown — which child drives the section height?
    planetPair: rect('.planet-port-pair'),
    planetInfo: rect('#planetSection .planet-info'),
    planetQuals: rect('#planetSection .planet-quals'),
    planetAct: rect('#planetSection .act'),
    // section box-model: padding + min-height that pad the row beyond content
    sectionBox: (() => { const cs = getComputedStyle(document.querySelector('#planetSection'));
      return { padTop: cs.paddingTop, padBottom: cs.paddingBottom, minH: cs.minHeight,
               align: cs.alignItems, gap: cs.gap, lineH: cs.lineHeight }; })(),
    // is the inner planet-section still painting its own card? (double-card check)
    innerCardBg: (() => { const el = document.querySelector('#planetSection');
      const cs = getComputedStyle(el); return { bg: cs.backgroundImage, borderW: cs.borderTopWidth }; })(),
  };
});
await browser.close();

// ── assertions ────────────────────────────────────────────────────────────
const results = [];
const check = (label, pass, detail) => { results.push({ label, pass, detail }); };

// (1) toggle vs mtitle + header
const capMult = m.toggle.h / m.mtitle.h;
check('toggle height <= 1.6x .mtitle height',
  m.toggle.h <= m.mtitle.h * 1.6,
  `toggle=${m.toggle.h}px  mtitle=${m.mtitle.h}px  ratio=${capMult.toFixed(2)}x (limit 1.60x)`);
check('toggle height <= .mhead height (not header-dominating)',
  m.toggle.h <= m.mhead.h + 0.5,
  `toggle=${m.toggle.h}px  mhead=${m.mhead.h}px`);

// (2) planet row within 1.3x star row
const rowMult = m.planetSection.h / m.starRow.h;
check('planet-section height within 1.3x star .row',
  rowMult <= 1.3,
  `planet=${m.planetSection.h}px  star.row=${m.starRow.h}px  ratio=${rowMult.toFixed(2)}x (limit 1.30x)`);

// (2b) double-card killed — inner section paints no gradient + no border
check('double-card killed: inner .planet-section has no background-image',
  m.innerCardBg.bg === 'none',
  `inner bg-image=${m.innerCardBg.bg}`);
check('double-card killed: inner .planet-section border width = 0',
  parseFloat(m.innerCardBg.borderW) === 0,
  `inner border-top-width=${m.innerCardBg.borderW}`);

// (3) station symmetric with planet
const symDiff = Math.abs(m.planetSection.h - m.stationSection.h);
check('station row symmetric with planet row (<=4px delta)',
  symDiff <= 4,
  `planet=${m.planetSection.h}px  station=${m.stationSection.h}px  delta=${symDiff.toFixed(2)}px`);

// icon sanity (was 16px absolute; should now track the small body font)
check('planet-icon no longer oversized (< 20px)',
  m.planetIcon.h < 20 && m.planetIcon.w < 20,
  `icon=${m.planetIcon.w}x${m.planetIcon.h}px  fs=${m.planetIcon.fs}`);

console.log('\n===== WO-SOLARUI computed-layout proof (1440x900, headless chromium) =====\n');
console.log('RAW GEOMETRY:');
console.log('  .mhead          ', JSON.stringify(m.mhead));
console.log('  .mtitle         ', JSON.stringify(m.mtitle));
console.log('  toggle          ', JSON.stringify(m.toggle));
console.log('  star .row       ', JSON.stringify(m.starRow));
console.log('  planet-pair(out)', JSON.stringify(m.planetPair));
console.log('  planet-section  ', JSON.stringify(m.planetSection));
console.log('  planet-info     ', JSON.stringify(m.planetInfo));
console.log('  planet-quals    ', JSON.stringify(m.planetQuals));
console.log('  planet-act(btn) ', JSON.stringify(m.planetAct));
console.log('  section box-model', JSON.stringify(m.sectionBox));
console.log('  station-section ', JSON.stringify(m.stationSection));
console.log('  planet-icon     ', JSON.stringify(m.planetIcon));
console.log('\nASSERTIONS:');
let allPass = true;
for (const r of results) { allPass = allPass && r.pass;
  console.log(`  ${r.pass ? 'PASS ✓' : 'FAIL ✗'}  ${r.label}\n           ${r.detail}`); }
console.log(`\n===== ${allPass ? 'ALL PASS ✓' : 'SOME FAILED ✗'} =====\n`);
process.exit(allPass ? 0 : 1);
