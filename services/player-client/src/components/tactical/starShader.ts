/**
 * starShader — the GLSL (vertex + fragment) for the AAA roiling-plasma sun
 * renderer (WO-AAA-SOLAR-TABLEAU phase 2, `StarDisc.tsx`; see
 * `audit/design-briefs/aaa-solar-tableau-2026-07-18.md` §"Sun (GLSL fragment
 * shader)"). Framework-light by design (no `three` import) — pure GLSL
 * strings + plain-TS per-kind parameter derivation, so it's independently
 * readable/testable without a GL context (mirrors tableauFxHarness.ts's own
 * "framework-light factory" split).
 *
 * Technique: ONE fullscreen shader quad (the vertex shader bypasses the
 * projection pipeline entirely — `gl_Position = vec4(position.xy, 0, 1)` —
 * so it always covers the whole canvas regardless of camera/resize). The
 * fragment shader masks itself down to the star disc + corona + occasional
 * CME plume purely from a `uCenterPx`/`uRadiusPx` uniform pair (set by
 * `StarDisc.tsx` from the harness's %-anchor mapper each frame) — this
 * covers the design brief's "corona = a larger additive quad BEHIND the
 * disc, ~5-6x disc" with a single draw call instead of two stacked quads:
 * the corona is just a wider radius test in the SAME per-pixel pass, which
 * is strictly cheaper (one shader invocation per pixel, not two overlapping
 * geometries) for an identical visual result.
 *
 * A point on the unit disc (`r = length(p) <= 1`) is treated as sitting on
 * a unit hemisphere facing the camera — `z = sqrt(1 - r*r)`, `normal =
 * vec3(p, z)` — the standard "2D sphere impostor" trick. Since the camera
 * looks straight on (`viewDir = (0,0,1)`), `dot(normal, viewDir) == z`,
 * which is exactly the brief's `pow(dot(normal,viewDir),k)` limb-darkening
 * term for free, and also gives the granulation noise a physically-
 * plausible curved sampling domain (advecting a 3D fBm over the normal,
 * not a flat 2D UV) without an actual 3D mesh.
 */

/** celestial_service.py:110-171's 11 star kinds — the shader has no
 *  kind-string awareness itself (all per-kind behavior is pre-baked into
 *  uniform VALUES by `starVisualParams` below); this list exists purely so
 *  a caller/test can iterate every kind without hand-copying the server's
 *  own table. */
export const STAR_KINDS = [
  'M_DWARF',
  'K_ORANGE',
  'G_YELLOW',
  'F_WHITE',
  'A_BLUE',
  'B_BLUE_GIANT',
  'O_BLUE_SUPER',
  'RED_GIANT',
  'WHITE_DWARF',
  'NEUTRON',
  'BLACK_HOLE',
] as const;

export type StarKind = (typeof STAR_KINDS)[number];

export const STAR_VERTEX_SHADER = /* glsl */ `
void main() {
  // Fullscreen-quad bypass: 'position' is auto-declared by THREE.ShaderMaterial's
  // <common> prologue. Skipping modelViewProjectionMatrix entirely means this
  // quad always exactly covers clip space -- no camera/resize sync needed.
  gl_Position = vec4(position.xy, 0.0, 1.0);
}
`;

export const STAR_FRAGMENT_SHADER = /* glsl */ `
precision highp float;

uniform float uTime;
uniform vec2  uResolution;   // canvas device-px size (cssWidth*dpr, cssHeight*dpr)
uniform vec2  uCenterPx;     // star disc center, device px, top-down (DOM convention)
uniform float uRadiusPx;     // star disc radius, device px

uniform vec3  uColorCore;    // hottest/brightest tone (flares, disc core highlight)
uniform vec3  uColorMid;     // base star.color (server canonical hex)
uniform vec3  uColorSpot;    // darkened sunspot tone

uniform float uGranulationScale;
uniform float uGranulationContrast;
uniform float uDomainWarpStrength;
uniform float uLimbPower;

uniform float uCoronaReach;      // radius multiplier where the corona glow fades to ~0
uniform float uFlareRate;        // 0..1 -- limb-flare frequency/intensity
uniform float uSunspotAmount;    // 0..1 -- 0 disables (NEUTRON: no visible spots)
uniform float uCmeRate;          // 0..1 -- 0 disables CME plumes entirely
uniform float uSeed;             // per-star deterministic phase offset (binary systems)

uniform float uIsBlackHole;      // 0 or 1 -- BLACK_HOLE swaps to the accretion-disk branch
uniform float uLensingStrength;  // BLACK_HOLE only -- asymmetric-brightening "beaming" amount

// ---------------------------------------------------------------------
// Ashima Arts / Stefan Gustavson 3D simplex noise (webgl-noise, MIT) --
// the standard, widely-reused GLSL snoise(vec3) implementation. Capped at
// 4 octaves below per the WO's PERF mandate.
// ---------------------------------------------------------------------
vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 permute(vec4 x) { return mod289(((x * 34.0) + 1.0) * x); }
vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }

float snoise(vec3 v) {
  const vec2 C = vec2(1.0 / 6.0, 1.0 / 3.0);
  const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);

  vec3 i  = floor(v + dot(v, C.yyy));
  vec3 x0 = v - i + dot(i, C.xxx);

  vec3 g = step(x0.yzx, x0.xyz);
  vec3 l = 1.0 - g;
  vec3 i1 = min(g.xyz, l.zxy);
  vec3 i2 = max(g.xyz, l.zxy);

  vec3 x1 = x0 - i1 + C.xxx;
  vec3 x2 = x0 - i2 + C.yyy;
  vec3 x3 = x0 - D.yyy;

  i = mod289(i);
  vec4 p = permute(permute(permute(
             i.z + vec4(0.0, i1.z, i2.z, 1.0))
           + i.y + vec4(0.0, i1.y, i2.y, 1.0))
           + i.x + vec4(0.0, i1.x, i2.x, 1.0));

  float n_ = 0.142857142857;
  vec3 ns = n_ * D.wyz - D.xzx;

  vec4 j = p - 49.0 * floor(p * ns.z * ns.z);

  vec4 x_ = floor(j * ns.z);
  vec4 y_ = floor(j - 7.0 * x_);

  vec4 x = x_ * ns.x + ns.yyyy;
  vec4 y = y_ * ns.x + ns.yyyy;
  vec4 h = 1.0 - abs(x) - abs(y);

  vec4 b0 = vec4(x.xy, y.xy);
  vec4 b1 = vec4(x.zw, y.zw);

  vec4 s0 = floor(b0) * 2.0 + 1.0;
  vec4 s1 = floor(b1) * 2.0 + 1.0;
  vec4 sh = -step(h, vec4(0.0));

  vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy;
  vec4 a1 = b1.xzyw + s1.xzyw * sh.zzww;

  vec3 p0 = vec3(a0.xy, h.x);
  vec3 p1 = vec3(a0.zw, h.y);
  vec3 p2 = vec3(a1.xy, h.z);
  vec3 p3 = vec3(a1.zw, h.w);

  vec4 norm = taylorInvSqrt(vec4(dot(p0, p0), dot(p1, p1), dot(p2, p2), dot(p3, p3)));
  p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;

  vec4 m = max(0.6 - vec4(dot(x0, x0), dot(x1, x1), dot(x2, x2), dot(x3, x3)), 0.0);
  m = m * m;
  return 42.0 * dot(m * m, vec4(dot(p0, x0), dot(p1, x1), dot(p2, x2), dot(p3, x3)));
}

// 4-octave fBm -- PERF-capped (WO mandate: "cap octaves 3-4").
float fbm3(vec3 p) {
  float value = 0.0;
  float amp = 0.5;
  float freq = 1.0;
  for (int i = 0; i < 4; i++) {
    value += amp * snoise(p * freq);
    freq *= 2.02;
    amp *= 0.5;
  }
  return value;
}

// Inigo Quilez domain-warp: a first fBm pass perturbs the SAMPLE COORDS of
// a second fBm pass -- this is what turns plain turbulence into "flow"
// (the granulation visibly advects/curls rather than just shimmering).
vec3 domainWarp(vec3 p, float t, float strength) {
  vec3 q = vec3(
    fbm3(p + vec3(0.0, 0.0, t)),
    fbm3(p + vec3(5.2, 1.3, t)),
    fbm3(p + vec3(1.7, 9.2, t))
  );
  return p + strength * q;
}

float hash1(float n) {
  return fract(sin(n) * 43758.5453123);
}

const float TAU = 6.28318530718;
const float PI = 3.14159265359;

// One CME plume slot per star: a slow random timer (period + per-cycle
// hash gate on uCmeRate) picks an eject angle and a 1.6s eased envelope;
// angular gaussian x radial band gives the "plume bursting off the limb"
// look. Deliberately gated OFF at uTime<=0 (reduced-motion's pinned frame)
// so the calm single frame can never freeze mid-burst.
float cmePlume(vec2 p, float r, float t) {
  if (uCmeRate <= 0.0 || t <= 0.0001) return 0.0;
  float period = 20.0 + 8.0 * hash1(uSeed * 91.7);
  float phaseT = t + uSeed * 137.0;
  float cycle = floor(phaseT / period);
  float localT = mod(phaseT, period);

  float fires = hash1(cycle * 12.9898 + uSeed * 3.7);
  if (fires > uCmeRate) return 0.0;

  float duration = 1.6;
  if (localT > duration) return 0.0;

  float envelope = sin(clamp(localT / duration, 0.0, 1.0) * PI);
  float burstAngle = hash1(cycle * 7.233 + uSeed) * TAU;
  float angle = atan(p.y, p.x);
  float da = abs(mod(angle - burstAngle + PI, TAU) - PI);
  float angularFalloff = exp(-(da * da) / (2.0 * 0.32 * 0.32));

  float plumeReach = 1.0 + envelope * 1.15;
  // GLSL's smoothstep(edge0, edge1, x) is spec-UNDEFINED when edge0>=edge1
  // -- plumeReach is always >=1.0 by construction, so the naive
  // smoothstep(plumeReach, 1.0, r) had its edges backwards on every single
  // call (verified: this NaN-poisoned the whole fragment's output whenever
  // this branch executed, blanking the ENTIRE star canvas -- see the monk
  // agent notebook's star-disc-cme-smoothstep-backwards-edges entry).
  // max(plumeReach, 1.001) guards the degenerate envelope=0 instant
  // (edge0==edge1, still spec-undefined) with a negligible epsilon.
  // 1.0 - smoothstep(...) restores the intended shape: 1 (bright) at r=1
  // (the plume's origin, the disc edge), fading to 0 at r=plumeReach (its
  // outer extent).
  float radialFalloff = (1.0 - smoothstep(1.0, max(plumeReach, 1.001), r)) * smoothstep(0.92, 1.05, r);

  return angularFalloff * radialFalloff * envelope;
}

// BLACK_HOLE's own distinct visual: no granulation/corona at all -- a dark
// event-horizon core (the transparent gap simply lets the space backdrop
// show through) ringed by an accretion disk with a slow swirl + an
// asymmetric "beaming" brightening (a common, well-understood simplified
// stand-in for full relativistic lensing -- one side of the ring reads
// brighter, which is the recognizable "black hole" silhouette without an
// actual light-bending simulation).
vec4 blackHole(vec2 p, float r) {
  float angle = atan(p.y, p.x);
  float diskInner = 0.75;
  float diskOuter = 1.45;
  if (r <= diskInner || r >= diskOuter) return vec4(0.0);

  float ringFalloff =
    smoothstep(diskInner, diskInner + 0.10, r) *
    (1.0 - smoothstep(diskOuter - 0.18, diskOuter, r));
  float swirl = fbm3(vec3(cos(angle) * 2.6, sin(angle) * 2.6, r * 2.0 - uTime * 0.35));
  float beaming = 1.0 + uLensingStrength * cos(angle - uTime * 0.06);
  vec3 hot = mix(uColorMid, uColorCore, clamp(0.5 + 0.5 * swirl, 0.0, 1.0));
  vec3 color = hot * ringFalloff * max(beaming, 0.12);
  return vec4(color, ringFalloff * 0.85);
}

void main() {
  // gl_FragCoord.y is bottom-up (GL convention); flip to match the
  // top-down DOM/mapper convention uCenterPx was built from.
  vec2 fragPos = vec2(gl_FragCoord.x, uResolution.y - gl_FragCoord.y);
  vec2 p = (fragPos - uCenterPx) / max(uRadiusPx, 1.0);
  float r = length(p);

  if (uIsBlackHole > 0.5) {
    gl_FragColor = blackHole(p, r);
    return;
  }

  vec3 color = vec3(0.0);
  float alpha = 0.0;

  // --- disc: granulation + domain-warp + sunspots + limb-flares + limb-darkening ---
  if (r < 1.02) {
    float rClamped = min(r, 1.0);
    float z = sqrt(max(0.0, 1.0 - rClamped * rClamped));
    vec3 normal = vec3(p, z);

    vec3 samplePos = normal * uGranulationScale + vec3(0.0, 0.0, uTime * 0.05);
    vec3 warped = domainWarp(samplePos, uTime * 0.15, uDomainWarpStrength);
    float granulation = fbm3(warped) * uGranulationContrast;
    float g = clamp(granulation * 0.5 + 0.5, 0.0, 1.0);

    vec3 baseColor = mix(uColorSpot, uColorMid, g);

    // sunspots -- low-freq high-amp field, clamped positive, drifting slowly
    float spotField = fbm3(normal * 1.3 + vec3(0.0, 0.0, uTime * 0.015) + uSeed);
    float spots = max(0.0, spotField - 0.35) * uSunspotAmount;
    baseColor = mix(baseColor, baseColor * 0.22, clamp(spots, 0.0, 1.0));

    // solar flares -- brightness spikes gated to the limb by a fast noise field
    float limbBand = smoothstep(0.55, 0.95, 1.0 - z);
    float flareNoise = fbm3(normal * 5.5 + vec3(0.0, 0.0, uTime * 1.6 + uSeed * 5.0));
    float flareGate = smoothstep(0.72, 0.93, flareNoise) * uFlareRate;
    float flares = limbBand * flareGate;
    baseColor = mix(baseColor, uColorCore, clamp(flares, 0.0, 1.0));

    float limb = pow(clamp(z, 0.0, 1.0), uLimbPower);
    vec3 diskColor = baseColor * mix(0.32, 1.0, limb);

    float discMask = 1.0 - smoothstep(0.985, 1.02, r);
    color += diskColor * discMask;
    alpha = max(alpha, discMask);
  }

  // --- corona: additive glow beyond the disc edge, falling off to uCoronaReach ---
  if (r >= 0.96 && r < uCoronaReach) {
    float coronaFalloff = 1.0 - smoothstep(1.0, uCoronaReach, r);
    coronaFalloff *= coronaFalloff;
    float coronaNoise = 0.6 + 0.4 * fbm3(vec3(p * 2.0, uTime * 0.22 + uSeed));
    vec3 coronaColor = uColorMid * coronaNoise;
    float coronaAlpha = coronaFalloff * 0.5;
    color += coronaColor * coronaAlpha;
    alpha = max(alpha, coronaAlpha);
  }

  // --- CME plume: highest-cost effect, evaluated last, gated by uCmeRate ---
  float plumeReach = max(uCoronaReach, 1.0) + 1.3;
  if (r < plumeReach) {
    float plume = cmePlume(p, r, uTime);
    if (plume > 0.0) {
      vec3 plumeColor = mix(uColorMid, uColorCore, 0.6);
      color += plumeColor * plume * 0.9;
      alpha = max(alpha, plume * 0.8);
    }
  }

  gl_FragColor = vec4(color, alpha);
}
`;

export interface StarVisualParams {
  colorCore: [number, number, number];
  colorMid: [number, number, number];
  colorSpot: [number, number, number];
  granulationScale: number;
  granulationContrast: number;
  domainWarpStrength: number;
  limbPower: number;
  coronaReach: number;
  flareRate: number;
  sunspotAmount: number;
  cmeRate: number;
  isBlackHole: boolean;
  lensingStrength: number;
}

/** '#rrggbb' -> 0..1 RGB tuple (this file's own tiny copy of the same
 *  conversion SolarSystemViewscreen.tsx's own local `hexToRgb` performs --
 *  that one isn't exported, and this module is deliberately `three`-free,
 *  so a shared util isn't worth the coupling for 4 lines of math). Falls
 *  back to a neutral mid-grey on an unparseable hex rather than throwing --
 *  a malformed server color should degrade, not crash the tableau. */
function hexToUnitRgb(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex?.trim() ?? '');
  if (!m) return [0.6, 0.6, 0.6];
  const n = parseInt(m[1], 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

function lighten(rgb: [number, number, number], amount: number): [number, number, number] {
  return [
    rgb[0] + (1 - rgb[0]) * amount,
    rgb[1] + (1 - rgb[1]) * amount,
    rgb[2] + (1 - rgb[2]) * amount,
  ];
}

function darken(rgb: [number, number, number], amount: number): [number, number, number] {
  return [rgb[0] * (1 - amount), rgb[1] * (1 - amount), rgb[2] * (1 - amount)];
}

type ParamOverrides = Partial<Omit<StarVisualParams, 'colorCore' | 'colorMid' | 'colorSpot'>>;

const DEFAULT_PARAMS: Omit<StarVisualParams, 'colorCore' | 'colorMid' | 'colorSpot' | 'isBlackHole'> = {
  granulationScale: 2.4,
  granulationContrast: 0.9,
  domainWarpStrength: 0.6,
  limbPower: 1.4,
  coronaReach: 1.35,
  flareRate: 0.35,
  sunspotAmount: 0.5,
  cmeRate: 0.4,
  lensingStrength: 0.0,
};

/** Per-kind overrides layered onto DEFAULT_PARAMS. G_YELLOW (Sol-like) has
 *  none -- it IS the default. Dimmer/cooler kinds get tighter coronas +
 *  lower contrast; hotter/bigger kinds get wider coronas + more flare
 *  activity; the three exotic remnants (RED_GIANT bloated+cool,
 *  WHITE_DWARF/NEUTRON tiny+fierce, BLACK_HOLE) get the brief's called-out
 *  distinct treatment. */
const KIND_OVERRIDES: Record<StarKind, ParamOverrides> = {
  M_DWARF: { granulationScale: 3.2, granulationContrast: 0.7, coronaReach: 1.18, flareRate: 0.55, cmeRate: 0.25 },
  K_ORANGE: { granulationScale: 2.8, granulationContrast: 0.8, coronaReach: 1.22, flareRate: 0.45, cmeRate: 0.3 },
  G_YELLOW: {},
  F_WHITE: { granulationScale: 2.1, granulationContrast: 0.85, coronaReach: 1.3, limbPower: 1.3 },
  A_BLUE: { granulationScale: 1.9, granulationContrast: 0.75, coronaReach: 1.4, flareRate: 0.5, cmeRate: 0.5 },
  B_BLUE_GIANT: { granulationScale: 1.6, granulationContrast: 0.7, coronaReach: 1.55, flareRate: 0.65, cmeRate: 0.65, limbPower: 1.2 },
  O_BLUE_SUPER: { granulationScale: 1.3, granulationContrast: 0.65, coronaReach: 1.7, flareRate: 0.8, cmeRate: 0.8, limbPower: 1.1 },
  // RED_GIANT: bloated+cool -- coarser/slower granulation, heavier sunspot
  // coverage, dimmer flares, a wide but soft corona.
  RED_GIANT: { granulationScale: 1.1, granulationContrast: 0.5, domainWarpStrength: 0.9, coronaReach: 1.6, flareRate: 0.2, sunspotAmount: 0.75, cmeRate: 0.55, limbPower: 1.6 },
  // WHITE_DWARF / NEUTRON: tiny+fierce -- fine-grained high-contrast
  // granulation, sharp limb, minimal-to-no corona/CME, near-constant flare.
  WHITE_DWARF: { granulationScale: 4.0, granulationContrast: 1.1, sunspotAmount: 0.1, coronaReach: 1.1, flareRate: 0.85, cmeRate: 0.05, limbPower: 2.2 },
  NEUTRON: { granulationScale: 5.0, granulationContrast: 1.3, sunspotAmount: 0.0, coronaReach: 1.08, flareRate: 0.95, cmeRate: 0.0, limbPower: 2.8 },
  // BLACK_HOLE: no corona/granulation at all (shader branches away from
  // the whole disc pass) -- the only params that matter are the ones the
  // blackHole() branch reads: colorMid/colorCore + uLensingStrength.
  BLACK_HOLE: { isBlackHole: true, coronaReach: 1.45, lensingStrength: 0.6 } as ParamOverrides & { isBlackHole: boolean },
};

/** Derives the full uniform-value set for one star from its server-canonical
 *  `kind` + `color` (celestial_service.py:110-171's STAR_COLORS). Pure
 *  function, deterministic, no GL/DOM -- `StarDisc.tsx` wraps the result
 *  into `THREE.Vector3`/`ShaderMaterial` uniforms. An unrecognized kind
 *  falls back to G_YELLOW's defaults rather than throwing (a future server
 *  kind shouldn't blank the sun). */
export function starVisualParams(kind: string, color: string): StarVisualParams {
  const override = (KIND_OVERRIDES as Record<string, ParamOverrides>)[kind] ?? {};
  const isBlackHole = Boolean((override as { isBlackHole?: boolean }).isBlackHole);
  const mid = hexToUnitRgb(color);
  return {
    ...DEFAULT_PARAMS,
    ...override,
    colorMid: mid,
    colorCore: lighten(mid, 0.55),
    colorSpot: darken(mid, 0.65),
    isBlackHole,
  };
}
