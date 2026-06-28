import React, { useEffect, useRef, useCallback } from 'react';
import type { QuantumScanResult } from '../../contexts/GameContext';

/**
 * QuantumBearingViewport — the ASTROGATION PLOT instrument of the Quantum
 * Drive console (ADR-0030 Phase 1: "a 3D minimap of sectors within roughly
 * 25 hop-units"). A pure instrument: the parent owns yaw/pitch/band state;
 * this component renders the chart and reports drag-to-aim yaw changes.
 *
 * PROJECTION DECISIONS (documented per section contract):
 *
 * - The chart stays FIXED (top-down plot of the galactic xy-plane) and THE
 *   SHIP GLYPH rotates — "the ship answers her helm" rather than the sky
 *   spinning around it. z is encoded in dot size + brightness (larger and
 *   brighter = above the plane, smaller and dimmer = below).
 *
 * - Compass mapping: server yaw is CCW-from-+x in the xy-plane (0 = +x,
 *   90 = +y). The console's slider ticks label yaw 0 as N and 90 as E, so
 *   world +x maps to screen-up and world +y to screen-right:
 *   screen = (cx + dy*k, cy - dx*k). Bearing yaw then reads like a real
 *   compass (increases clockwise on screen).
 *
 * - SCALE: fixed viewport radius = 16 inter-sector spacings, all bands.
 *   Band arcs at 6/8/10/15 spacings all fit on-plot (extended's 15 sits
 *   just inside the rim), and a constant scale means dots never re-project
 *   when the pilot flips bands. Minimap data reaches 25 spacings; dots
 *   beyond the 16-spacing rim are not drawn and an honest "+N BEYOND PLOT"
 *   tick at the rim reports how many were clipped.
 *
 * - Cone membership halos use the true 3D cone test (same 15° half-angle
 *   dot-product the server runs), so what glows violet is exactly what the
 *   echo scan would sweep — not a 2D approximation.
 *
 * - DISCLOSURE (ADR-0031): the endpoint feeds anonymous positions only —
 *   not even sector ids ("the specific sector ID is not disclosed"). All
 *   dots are identical anonymous contacts; nothing here leaks identity,
 *   type, activity, or presence. The fuzzy scan is the only telescope.
 *
 * PERFORMANCE: requestAnimationFrame runs ONLY while the bearing ease is in
 * flight, a phase animation (scanning/charging) or echo ripple is active,
 * or the idle cone shimmer is permitted (tab focused, no reduced-motion).
 * Otherwise the plot renders once and the loop stops. When the ONLY reason
 * to animate is the idle shimmer/twinkle, draws are throttled to ~12fps
 * (rAF keeps queueing, but frames inside SHIMMER_FRAME_MS are skipped);
 * easing, phases and the echo ripple always run at full 60fps.
 */

export interface MinimapSector {
  dx: number;
  dy: number;
  dz: number;
}

export type ViewportPhase = 'idle' | 'scanning' | 'charging';
export type ViewportRangeBand = 'near' | 'mid' | 'far' | 'extended';

interface QuantumBearingViewportProps {
  yawDeg: number;
  pitchDeg: number;
  rangeBand: ViewportRangeBand;
  onBearingChange: (yaw: number, pitch: number) => void;
  phase: ViewportPhase;
  /** Inter-sector spacing in absolute coordinate units (server-computed).
   *  null while the chart is loading/unavailable — rings and cone still
   *  render (they're defined in spacings), only dots need it. */
  spacing: number | null;
  /** null = chart fetch failed → render the instrument WITHOUT dots and
   *  show the amber CHART UNAVAILABLE notice. Empty array = loaded/empty. */
  sectors: MinimapSector[] | null;
  /** Chart fetch in flight (distinct from failed and from loaded-empty):
   *  renders the violet CHARTING… chip instead of the amber warn. */
  chartLoading?: boolean;
  /** How far (in spacings) the chart is complete (server cap honesty).
   *  Beyond min(16, this) the plot annulus is dimmed + hatched so a
   *  truncated chart never reads as empty space. null = assume complete. */
  completeRadiusSpacings?: number | null;
  /** A fresh scan result triggers the echo-return ripple — keyed on
   *  expires_at CHANGING so back-to-back scans still ripple. */
  scanResult?: QuantumScanResult | null;
}

// Band geometry in spacings (mirrors RANGE_BANDS server-side)
const BAND_LIMITS: Record<ViewportRangeBand, { min: number; max: number }> = {
  near: { min: 5, max: 6 },
  mid: { min: 7, max: 8 },
  far: { min: 9, max: 10 },
  extended: { min: 12, max: 15 },
};

const VIEW_RADIUS_SPACINGS = 16; // fixed plot scale — see projection notes
const CONE_HALF_ANGLE_DEG = 15; // matches server CONE_HALF_ANGLE_DEG
const EASE_TIME_CONSTANT_MS = 80; // exp ease-out; ~95% settled at ~250ms
const SWEEP_PERIOD_MS = 1200;
const ECHO_RIPPLE_MS = 800;
const CHARGE_COLLAPSE_MS = 1800;
const SHIMMER_FRAME_MS = 70; // ~12fps draw cadence when ONLY shimmer animates

const VIOLET = '#7B2FFF';

/** Shortest-arc yaw delta in degrees, in (-180, 180]. */
const yawDelta = (from: number, to: number): number => {
  let d = (to - from) % 360;
  if (d > 180) d -= 360;
  if (d <= -180) d += 360;
  return d;
};

const QuantumBearingViewport: React.FC<QuantumBearingViewportProps> = ({
  yawDeg,
  pitchDeg,
  rangeBand,
  onBearingChange,
  phase,
  spacing,
  sectors,
  chartLoading = false,
  completeRadiusSpacings = null,
  scanResult = null,
}) => {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // Latest props, readable from the rAF closure without re-binding it
  const propsRef = useRef({
    yawDeg, pitchDeg, rangeBand, phase, spacing, sectors, completeRadiusSpacings,
  });
  propsRef.current = {
    yawDeg, pitchDeg, rangeBand, phase, spacing, sectors, completeRadiusSpacings,
  };

  // Helm animation state — display bearing chases the target bearing
  const displayRef = useRef({ yaw: yawDeg, pitch: pitchDeg, lastT: 0 });
  // One-shot animation timestamps (0 = inactive)
  const echoStartRef = useRef(0);
  const phaseStartRef = useRef(0);
  const dragRef = useRef(false);
  const sizeRef = useRef({ w: 0, h: 0, dpr: 1 });
  const rafRef = useRef(0);
  const runningRef = useRef(false);
  const focusedRef = useRef(true);
  const reducedMotionRef = useRef(false);
  const lastShimmerDrawRef = useRef(0);

  // --- echo-return ripple: keyed on expires_at CHANGING, not null→non-null,
  // so back-to-back scans (GAME_TIME_SCALE-compressed cooldowns) still
  // ripple even when the previous result hasn't faded yet ---
  const prevScanExpiryRef = useRef<string | null>(null);
  useEffect(() => {
    const expiry = scanResult?.expires_at ?? null;
    if (expiry && expiry !== prevScanExpiryRef.current) {
      echoStartRef.current = performance.now();
      kickLoop();
    }
    prevScanExpiryRef.current = expiry;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scanResult]);

  // --- phase transitions restart the phase clock ---
  useEffect(() => {
    phaseStartRef.current = performance.now();
    kickLoop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase]);

  // --- bearing prop changes wake the ease animation ---
  useEffect(() => {
    kickLoop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [yawDeg, pitchDeg, rangeBand, spacing, sectors]);

  /** Why the loop is alive. `full` reasons (easing / phase / echo) demand
   *  60fps; `shimmer` alone is throttled to SHIMMER_FRAME_MS in the loop.
   *  Shimmer keeps the prefers-reduced-motion and focus/blur gates. */
  const animReasons = useCallback((): { full: boolean; shimmer: boolean } => {
    const p = propsRef.current;
    const d = displayRef.current;
    const easing =
      Math.abs(yawDelta(d.yaw, p.yawDeg)) > 0.05 ||
      Math.abs(p.pitchDeg - d.pitch) > 0.05;
    const echoActive =
      echoStartRef.current > 0 &&
      performance.now() - echoStartRef.current < ECHO_RIPPLE_MS;
    return {
      full: easing || p.phase !== 'idle' || echoActive,
      shimmer: !reducedMotionRef.current && focusedRef.current,
    };
  }, []);

  // ===========================================================================
  // Drawing
  // ===========================================================================
  const draw = useCallback((now: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const { w, h, dpr } = sizeRef.current;
    if (w <= 0 || h <= 0) return;

    const p = propsRef.current;
    const d = displayRef.current;
    const reduced = reducedMotionRef.current;

    // --- advance the helm ease (exponential ease-out toward the target) ---
    const dt = d.lastT > 0 ? Math.min(64, now - d.lastT) : 16;
    d.lastT = now;
    const k = 1 - Math.exp(-dt / EASE_TIME_CONSTANT_MS);
    const dYaw = yawDelta(d.yaw, p.yawDeg);
    const dPitch = p.pitchDeg - d.pitch;
    d.yaw = (d.yaw + dYaw * k + 360) % 360;
    d.pitch += dPitch * k;
    if (Math.abs(yawDelta(d.yaw, p.yawDeg)) < 0.05) d.yaw = p.yawDeg;
    if (Math.abs(p.pitchDeg - d.pitch) < 0.05) d.pitch = p.pitchDeg;
    const turning = Math.abs(yawDelta(d.yaw, p.yawDeg)) > 1.5;
    const turnSign = Math.sign(yawDelta(d.yaw, p.yawDeg)); // +1 = swinging clockwise on screen

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.font = '7px "Courier New", monospace';

    const cx = w / 2;
    const cy = h / 2;
    const plotR = Math.min(w, h) / 2 - 14; // inset for frame + labels
    const pxPerSpacing = plotR / VIEW_RADIUS_SPACINGS;

    const band = BAND_LIMITS[p.rangeBand];
    const yawRad = (d.yaw * Math.PI) / 180;
    const pitchRad = (d.pitch * Math.PI) / 180;
    // Screen-space bearing angle for ctx.arc()/vectors: yaw 0 = up
    const screenAng = yawRad - Math.PI / 2;
    const dirX = Math.cos(screenAng);
    const dirY = Math.sin(screenAng);
    const halfAngle = (CONE_HALF_ANGLE_DEG * Math.PI) / 180;

    // --- charging collapse factor: cone draws inward over ~1.8s ---
    const phaseElapsed = now - phaseStartRef.current;
    const collapse =
      p.phase === 'charging'
        ? Math.max(0.06, 1 - phaseElapsed / CHARGE_COLLAPSE_MS)
        : 1;
    // Pitch foreshortening: the painted wedge is the cone's xy-projection,
    // so its length shrinks by cos(pitch) — the same foreshortening the
    // ship glyph gets — and it fades toward |pitch| = 90°, where the cone
    // points straight out of the chart plane. The 3D dot-product halo test
    // below is deliberately untouched (it is the truth; this is the paint).
    const pitchCos = Math.max(0, Math.cos(pitchRad));
    const pitchFade = Math.max(0, 1 - Math.abs(d.pitch) / 90);
    const coneLen = Math.min(band.max * pxPerSpacing, plotR) * collapse * pitchCos;

    // --- background vignette ---
    const vg = ctx.createRadialGradient(cx, cy, plotR * 0.2, cx, cy, plotR);
    vg.addColorStop(0, 'rgba(123, 47, 255, 0.04)');
    vg.addColorStop(1, 'rgba(4, 1, 12, 0.25)');
    ctx.fillStyle = vg;
    ctx.beginPath();
    ctx.arc(cx, cy, plotR, 0, Math.PI * 2);
    ctx.fill();

    // --- range band rings (faint), selected band annulus brightened ---
    const ringRadii: Array<[number, boolean]> = [
      [6, p.rangeBand === 'near'],
      [8, p.rangeBand === 'mid'],
      [10, p.rangeBand === 'far'],
      [15, p.rangeBand === 'extended'],
    ];
    // selected annulus fill (min..max)
    ctx.beginPath();
    ctx.arc(cx, cy, band.max * pxPerSpacing, 0, Math.PI * 2);
    ctx.arc(cx, cy, band.min * pxPerSpacing, 0, Math.PI * 2, true);
    ctx.fillStyle = 'rgba(123, 47, 255, 0.06)';
    ctx.fill();
    for (const [r, selected] of ringRadii) {
      ctx.beginPath();
      ctx.arc(cx, cy, r * pxPerSpacing, 0, Math.PI * 2);
      ctx.strokeStyle = selected
        ? 'rgba(123, 47, 255, 0.55)'
        : 'rgba(0, 217, 255, 0.10)';
      ctx.lineWidth = selected ? 1 : 0.5;
      ctx.stroke();
    }
    // selected band min arc (annulus inner edge)
    ctx.beginPath();
    ctx.arc(cx, cy, band.min * pxPerSpacing, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(123, 47, 255, 0.3)';
    ctx.lineWidth = 0.5;
    ctx.stroke();
    // rim
    ctx.beginPath();
    ctx.arc(cx, cy, plotR, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(0, 217, 255, 0.18)';
    ctx.lineWidth = 1;
    ctx.stroke();

    // Cardinal tick labels on the rim (yaw 0=N per the console's compass)
    ctx.fillStyle = 'rgba(0, 217, 255, 0.35)';
    ctx.textAlign = 'center';
    ctx.fillText('000', cx, cy - plotR + 8);
    ctx.fillText('180', cx, cy + plotR - 3);
    ctx.textAlign = 'left';
    ctx.fillText('270', cx - plotR + 2, cy + 2.5);
    ctx.textAlign = 'right';
    ctx.fillText('090', cx + plotR - 2, cy + 2.5);

    // --- cap honesty: beyond the server's complete-coverage radius the
    // chart THINS (the 400-nearest cap truncated it). Dim + hatch that
    // annulus and tick the boundary so truncated coverage never reads as
    // genuinely empty space. ---
    const thinR = Math.min(
      VIEW_RADIUS_SPACINGS,
      p.completeRadiusSpacings ?? Number.POSITIVE_INFINITY
    );
    if (thinR < VIEW_RADIUS_SPACINGS) {
      const innerR = Math.max(0, thinR * pxPerSpacing);
      ctx.save();
      ctx.beginPath();
      ctx.arc(cx, cy, plotR, 0, Math.PI * 2);
      ctx.arc(cx, cy, innerR, 0, Math.PI * 2, true);
      ctx.clip();
      ctx.fillStyle = 'rgba(4, 1, 12, 0.45)';
      ctx.fillRect(0, 0, w, h);
      // subtle diagonal hatch — chart-paper "no data" texture
      ctx.strokeStyle = 'rgba(0, 217, 255, 0.05)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (let x = -h; x < w + h; x += 7) {
        ctx.moveTo(x, 0);
        ctx.lineTo(x + h, h);
      }
      ctx.stroke();
      ctx.restore();
      // dashed boundary ring + CHART THINS rim tick (lower-left, clear of
      // the cardinal labels and the top-right BEYOND PLOT counter)
      ctx.beginPath();
      ctx.arc(cx, cy, innerR, 0, Math.PI * 2);
      ctx.setLineDash([3, 4]);
      ctx.strokeStyle = 'rgba(0, 217, 255, 0.16)';
      ctx.lineWidth = 0.75;
      ctx.stroke();
      ctx.setLineDash([]);
      const tickAng = (3 * Math.PI) / 4; // screen lower-left
      const tx2 = cx + Math.cos(tickAng) * innerR;
      const ty2 = cy + Math.sin(tickAng) * innerR;
      ctx.beginPath();
      ctx.moveTo(tx2, ty2);
      ctx.lineTo(tx2 + Math.cos(tickAng) * 5, ty2 + Math.sin(tickAng) * 5);
      ctx.strokeStyle = 'rgba(0, 217, 255, 0.45)';
      ctx.lineWidth = 1;
      ctx.stroke();
      const labelR = Math.min(plotR - 8, innerR + 12);
      ctx.fillStyle = 'rgba(0, 217, 255, 0.4)';
      ctx.textAlign = 'center';
      ctx.fillText(
        'CHART THINS',
        cx + Math.cos(tickAng) * labelR,
        cy + Math.sin(tickAng) * labelR + 2
      );
    }

    // --- bearing cone (translucent violet, crisp edges, slow shimmer) ---
    const shimmer =
      reduced ? 0.16 : 0.13 + 0.06 * (0.5 + 0.5 * Math.sin(now / 1700));
    const tipX = cx + dirX * coneLen;
    const tipY = cy + dirY * coneLen;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, coneLen, screenAng - halfAngle, screenAng + halfAngle);
    ctx.closePath();
    const cg = ctx.createRadialGradient(cx, cy, 2, cx, cy, Math.max(coneLen, 4));
    cg.addColorStop(0, `rgba(123, 47, 255, ${((shimmer + 0.1) * pitchFade).toFixed(3)})`);
    cg.addColorStop(1, `rgba(123, 47, 255, ${(shimmer * 0.35 * pitchFade).toFixed(3)})`);
    ctx.fillStyle = cg;
    ctx.fill();
    ctx.strokeStyle = `rgba(123, 47, 255, ${(0.75 * pitchFade).toFixed(3)})`;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(
      cx + Math.cos(screenAng - halfAngle) * coneLen,
      cy + Math.sin(screenAng - halfAngle) * coneLen
    );
    ctx.moveTo(cx, cy);
    ctx.lineTo(
      cx + Math.cos(screenAng + halfAngle) * coneLen,
      cy + Math.sin(screenAng + halfAngle) * coneLen
    );
    ctx.stroke();

    // Extended band reaching past the rim would clip — with the fixed
    // 16-spacing scale extended (15) just fits, but guard anyway: if the
    // band max exceeds the plot, mark the rim with a BEYOND PLOT tick.
    if (band.max * pxPerSpacing > plotR + 0.5) {
      ctx.fillStyle = 'rgba(255, 176, 0, 0.8)';
      ctx.textAlign = 'center';
      ctx.fillText('BEYOND PLOT', tipX, tipY - 4);
    }

    // --- starfield dots + cone-member halos (3D cone test, server-true) ---
    let clippedCount = 0;
    if (p.sectors && p.sectors.length > 0 && p.spacing && p.spacing > 0) {
      const sp = p.spacing;
      const maxConeDist = band.max * sp;
      // 3D bearing unit vector in WORLD coords (server convention)
      const bx = Math.cos(pitchRad) * Math.cos(yawRad);
      const by = Math.cos(pitchRad) * Math.sin(yawRad);
      const bz = Math.sin(pitchRad);
      const cosThresh = Math.cos(halfAngle);
      const twinkleOn = !reduced && (p.phase !== 'idle' || focusedRef.current);

      for (let i = 0; i < p.sectors.length; i++) {
        const s = p.sectors[i];
        // world → screen: +x up, +y right (compass mapping, see header)
        const sx = cx + (s.dy / sp) * pxPerSpacing;
        const sy = cy - (s.dx / sp) * pxPerSpacing;
        const planarR = Math.hypot(sx - cx, sy - cy);
        if (planarR > plotR - 2) {
          clippedCount++;
          continue;
        }
        // z encoding: above plane = larger/brighter, below = smaller/dimmer
        const zT = Math.max(-1, Math.min(1, s.dz / (sp * 8)));
        let alpha = 0.42 + zT * 0.28;
        const dotR = 1.5 + zT * 0.8;
        if (twinkleOn) {
          // cheap deterministic parallax twinkle keyed off the array index
          // (the payload carries no sector ids per ADR-0031)
          alpha += 0.08 * Math.sin(now / 900 + (i % 17) * 1.7);
        }
        alpha = Math.max(0.1, Math.min(0.85, alpha));

        // true 3D cone membership (matches the server's sweep)
        const dist3 = Math.hypot(s.dx, s.dy, s.dz);
        const inCone =
          dist3 > 0 &&
          dist3 <= maxConeDist * collapse &&
          (s.dx * bx + s.dy * by + s.dz * bz) / dist3 >= cosThresh;

        ctx.beginPath();
        ctx.arc(sx, sy, Math.max(0.6, dotR), 0, Math.PI * 2);
        ctx.fillStyle = `rgba(0, 217, 255, ${alpha.toFixed(3)})`;
        ctx.fill();
        if (inCone) {
          ctx.beginPath();
          ctx.arc(sx, sy, dotR + 2.6, 0, Math.PI * 2);
          ctx.strokeStyle = 'rgba(123, 47, 255, 0.85)';
          ctx.lineWidth = 1;
          ctx.stroke();
        }
      }
      if (clippedCount > 0) {
        ctx.fillStyle = 'rgba(0, 217, 255, 0.3)';
        ctx.textAlign = 'right';
        ctx.fillText(`+${clippedCount} BEYOND PLOT`, w - 8, 18);
      }
    }

    // --- scanning sweep pulse: ship → cone tip, repeating ---
    if (p.phase === 'scanning') {
      const prog = ((now - phaseStartRef.current) % SWEEP_PERIOD_MS) / SWEEP_PERIOD_MS;
      const r = Math.max(2, prog * coneLen);
      ctx.beginPath();
      ctx.arc(cx, cy, r, screenAng - halfAngle, screenAng + halfAngle);
      ctx.strokeStyle = `rgba(224, 204, 255, ${(0.9 * (1 - prog)).toFixed(3)})`;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }

    // --- echo-return ripple: cone tip contracting back to the ship ---
    if (echoStartRef.current > 0) {
      const eProg = (now - echoStartRef.current) / ECHO_RIPPLE_MS;
      if (eProg < 1) {
        const r = Math.max(2, (1 - eProg) * coneLen);
        ctx.beginPath();
        ctx.arc(cx, cy, r, screenAng - halfAngle, screenAng + halfAngle);
        ctx.strokeStyle = `rgba(123, 47, 255, ${(0.95 * (1 - eProg * 0.5)).toFixed(3)})`;
        ctx.lineWidth = 2;
        ctx.stroke();
      } else {
        echoStartRef.current = 0;
      }
    }

    // --- charging: starlines streak along the bearing (translation tunnel) ---
    if (p.phase === 'charging' && !reduced) {
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(screenAng);
      const streaks = 12;
      for (let i = 0; i < streaks; i++) {
        // deterministic pseudo-random lane + phase per streak index
        const lane = (((i * 2654435761) % 100) / 100 - 0.5) * plotR * 0.9;
        const speed = 0.18 + ((i * 40503) % 100) / 700;
        const along = ((now * speed + i * 173) % (plotR * 2)) - plotR * 0.2;
        const len = 10 + ((i * 9973) % 12);
        const a = Math.max(0, 0.5 - Math.abs(lane) / (plotR * 1.4));
        ctx.beginPath();
        ctx.moveTo(along, lane);
        ctx.lineTo(along + len, lane);
        ctx.strokeStyle = `rgba(224, 204, 255, ${a.toFixed(3)})`;
        ctx.lineWidth = 0.8;
        ctx.stroke();
      }
      ctx.restore();
    }

    // --- the ship: vector Warp Jumper glyph, rotated to the display yaw ---
    let shipX = cx;
    let shipY = cy;
    if (p.phase === 'charging' && !reduced) {
      shipX += Math.random() * 2 - 1; // ±1px translation-stress jitter
      shipY += Math.random() * 2 - 1;
    }
    ctx.save();
    ctx.translate(shipX, shipY);
    ctx.rotate(yawRad); // compass: glyph drawn nose-up, rotate CW with yaw
    // pitch foreshortening along the hull axis
    ctx.scale(1, Math.max(0.35, Math.cos(pitchRad)));
    ctx.strokeStyle = '#e0ccff';
    ctx.lineWidth = 1.2;
    ctx.shadowColor = VIOLET;
    ctx.shadowBlur = 6;
    ctx.beginPath();
    // elongated hull (4 segments)
    ctx.moveTo(0, -11);
    ctx.lineTo(3, 5);
    ctx.lineTo(0, 3);
    ctx.lineTo(-3, 5);
    ctx.closePath();
    // twin nacelle pylons + nacelle bodies (4 segments)
    ctx.moveTo(2.5, 0);
    ctx.lineTo(6.5, 3);
    ctx.moveTo(6.5, 0);
    ctx.lineTo(6.5, 8);
    ctx.moveTo(-2.5, 0);
    ctx.lineTo(-6.5, 3);
    ctx.moveTo(-6.5, 0);
    ctx.lineTo(-6.5, 8);
    ctx.stroke();
    ctx.shadowBlur = 0;
    // RCS thruster ticks on the turning side while the helm answers
    if (turning && !reduced) {
      const side = -turnSign; // thrusters fire opposite the swing direction
      ctx.strokeStyle = 'rgba(0, 217, 255, 0.9)';
      ctx.lineWidth = 0.8;
      ctx.beginPath();
      ctx.moveTo(side * 3.5, -8);
      ctx.lineTo(side * 6.5, -9.5);
      ctx.moveTo(side * 3.2, -5.5);
      ctx.lineTo(side * 5.8, -6.5);
      ctx.stroke();
    }
    ctx.restore();

    // --- pitch ladder beside the ship (chevron slides -90..+90) ---
    const ladderX = cx + 26;
    const ladderH = 24;
    ctx.strokeStyle = 'rgba(0, 217, 255, 0.35)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(ladderX, cy - ladderH);
    ctx.lineTo(ladderX, cy + ladderH);
    // ticks at +90 / 0 / -90
    ctx.moveTo(ladderX - 2, cy - ladderH);
    ctx.lineTo(ladderX + 2, cy - ladderH);
    ctx.moveTo(ladderX - 3, cy);
    ctx.lineTo(ladderX + 3, cy);
    ctx.moveTo(ladderX - 2, cy + ladderH);
    ctx.lineTo(ladderX + 2, cy + ladderH);
    ctx.stroke();
    const chevY = cy - (d.pitch / 90) * ladderH;
    ctx.strokeStyle = '#c9a8ff';
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(ladderX + 6, chevY - 3);
    ctx.lineTo(ladderX + 2.5, chevY);
    ctx.lineTo(ladderX + 6, chevY + 3);
    ctx.stroke();

    // --- HUD frame: thin violet border + corner ticks ---
    ctx.strokeStyle = 'rgba(123, 47, 255, 0.45)';
    ctx.lineWidth = 1;
    ctx.strokeRect(0.5, 0.5, w - 1, h - 1);
    ctx.strokeStyle = 'rgba(123, 47, 255, 0.9)';
    ctx.lineWidth = 1.5;
    const tick = 9;
    ctx.beginPath();
    // four corner L-ticks
    ctx.moveTo(0.5, tick); ctx.lineTo(0.5, 0.5); ctx.lineTo(tick, 0.5);
    ctx.moveTo(w - tick, 0.5); ctx.lineTo(w - 0.5, 0.5); ctx.lineTo(w - 0.5, tick);
    ctx.moveTo(w - 0.5, h - tick); ctx.lineTo(w - 0.5, h - 0.5); ctx.lineTo(w - tick, h - 0.5);
    ctx.moveTo(tick, h - 0.5); ctx.lineTo(0.5, h - 0.5); ctx.lineTo(0.5, h - tick);
    ctx.stroke();
  }, []);

  // ===========================================================================
  // rAF loop control — runs only while something is animating (see header)
  // ===========================================================================
  const loop = useCallback(
    (t: number) => {
      const reasons = animReasons();
      // Idle-shimmer throttle: when shimmer/twinkle is the ONLY animation,
      // skip draws inside SHIMMER_FRAME_MS (~12fps) but keep requeueing so
      // an ease/phase/echo can resume 60fps on its very next frame.
      if (reasons.full || t - lastShimmerDrawRef.current >= SHIMMER_FRAME_MS) {
        draw(t);
        lastShimmerDrawRef.current = t;
      }
      if (reasons.full || reasons.shimmer) {
        rafRef.current = requestAnimationFrame(loop);
      } else {
        runningRef.current = false;
        displayRef.current.lastT = 0;
      }
    },
    [draw, animReasons]
  );

  const kickLoop = useCallback(() => {
    // A kick means something changed — let the next frame draw immediately
    // even if the shimmer throttle would otherwise skip it.
    lastShimmerDrawRef.current = 0;
    if (runningRef.current) return;
    runningRef.current = true;
    displayRef.current.lastT = 0;
    rafRef.current = requestAnimationFrame(loop);
  }, [loop]);

  // Mount: reduced-motion + focus tracking, ResizeObserver, initial draw
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    reducedMotionRef.current = mq.matches;
    const onMq = (e: MediaQueryListEvent) => {
      reducedMotionRef.current = e.matches;
      kickLoop();
    };
    mq.addEventListener('change', onMq);

    focusedRef.current = document.visibilityState === 'visible' && document.hasFocus();
    const onFocus = () => {
      focusedRef.current = true;
      kickLoop();
    };
    const onBlur = () => {
      focusedRef.current = false;
    };
    const onVis = () => {
      focusedRef.current = document.visibilityState === 'visible' && document.hasFocus();
      if (focusedRef.current) kickLoop();
    };
    window.addEventListener('focus', onFocus);
    window.addEventListener('blur', onBlur);
    document.addEventListener('visibilitychange', onVis);

    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    const ro = new ResizeObserver(() => {
      if (!wrap || !canvas) return;
      const rect = wrap.getBoundingClientRect();
      const cssW = Math.max(0, Math.floor(rect.width));
      // ~square instrument, clamped to the console band height
      const cssH = Math.max(200, Math.min(260, Math.round(cssW * 0.72)));
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
      canvas.style.height = `${cssH}px`;
      sizeRef.current = { w: cssW, h: cssH, dpr };
      kickLoop();
    });
    if (wrap) ro.observe(wrap);

    kickLoop();
    return () => {
      mq.removeEventListener('change', onMq);
      window.removeEventListener('focus', onFocus);
      window.removeEventListener('blur', onBlur);
      document.removeEventListener('visibilitychange', onVis);
      ro.disconnect();
      cancelAnimationFrame(rafRef.current);
      runningRef.current = false;
    };
  }, [kickLoop]);

  // ===========================================================================
  // Drag-to-aim: pointer angle around the plot center = yaw. Pitch stays on
  // the console's vertical slider (the two-axis scheme that needs no
  // keyboard modifiers); we pass the current pitch through unchanged.
  // ===========================================================================
  const yawFromPointer = (e: React.PointerEvent<HTMLCanvasElement>): number => {
    const rect = e.currentTarget.getBoundingClientRect();
    const sx = e.clientX - (rect.left + rect.width / 2);
    const sy = e.clientY - (rect.top + rect.height / 2);
    // screen → world: x = -sy, y = sx; yaw = atan2(y, x), compass-normalized
    const deg = (Math.atan2(sx, -sy) * 180) / Math.PI;
    return Math.round((deg + 360) % 360);
  };

  const handlePointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (e.button !== 0 && e.pointerType === 'mouse') return;
    dragRef.current = true;
    e.currentTarget.setPointerCapture(e.pointerId);
    onBearingChange(yawFromPointer(e), propsRef.current.pitchDeg);
  };
  const handlePointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (!dragRef.current) return;
    onBearingChange(yawFromPointer(e), propsRef.current.pitchDeg);
  };
  const handlePointerEnd = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (!dragRef.current) return;
    dragRef.current = false;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
  };

  const pitchSigned = `${pitchDeg >= 0 ? '+' : '−'}${Math.abs(pitchDeg).toFixed(1)}`;

  return (
    <div className="qbv-wrap" ref={wrapRef}>
      <div className="qbv-header">ASTROGATION PLOT</div>
      <canvas
        ref={canvasRef}
        className="qbv-canvas"
        role="application"
        aria-label="Astrogation plot. Drag to set the yaw bearing; use the pitch slider for elevation."
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerEnd}
        onPointerCancel={handlePointerEnd}
      />
      <div className="qbv-scanlines" aria-hidden="true" />
      <div className="qbv-readout" aria-live="off">
        BRG {yawDeg.toFixed(1)}° / PIT {pitchSigned}°
      </div>
      {chartLoading ? (
        <div className="qbv-chart-loading" role="status">
          CHARTING…
        </div>
      ) : sectors === null ? (
        <div className="qbv-chart-warn" role="status">
          CHART UNAVAILABLE — BEARING ONLY
        </div>
      ) : null}
    </div>
  );
};

export default QuantumBearingViewport;
