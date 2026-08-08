import React, { useMemo } from 'react';
import './orbital-scan-view.css';

/**
 * Orbital-scan "continent" silhouette with cosmetic candidate-spot markers.
 *
 * ADR-0091 explicitly scopes this as presentational-only in v1: the dots are
 * flavor, seeded deterministically from the planet id so the view is stable
 * across re-renders, but they carry NO backend state and must never be wired
 * to per-spot data (a real per-spot pick doesn't exist server-side — the
 * actual result only resolves at expedition launch). Do not add onClick /
 * selection semantics to individual dots.
 */

interface OrbitalScanViewProps {
  planetId: string;
  planetName?: string;
  /** Loose visual hint only (e.g. planet.type) — never gates anything here. */
  planetType?: string | null;
}

// Small deterministic PRNG (mulberry32) seeded from the planet id so the
// cosmetic silhouette + dots are stable per-planet without any server call.
function seededRandom(seedStr: string): () => number {
  let h = 1779033703 ^ seedStr.length;
  for (let i = 0; i < seedStr.length; i++) {
    h = Math.imul(h ^ seedStr.charCodeAt(i), 3432918353);
    h = (h << 13) | (h >>> 19);
  }
  let a = h >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** A cosmetic closed-path "continent" outline built from a few noisy rings. */
function buildSilhouettePath(rand: () => number): string {
  const cx = 100;
  const cy = 100;
  const points = 10;
  const baseR = 55;
  const coords: Array<[number, number]> = [];
  for (let i = 0; i < points; i++) {
    const angle = (i / points) * Math.PI * 2;
    const r = baseR + (rand() - 0.5) * 30;
    coords.push([cx + Math.cos(angle) * r, cy + Math.sin(angle) * r]);
  }
  const [first, ...rest] = coords;
  const d = [`M ${first[0].toFixed(1)} ${first[1].toFixed(1)}`]
    .concat(rest.map(([x, y]) => `L ${x.toFixed(1)} ${y.toFixed(1)}`))
    .concat(['Z']);
  return d.join(' ');
}

const OrbitalScanView: React.FC<OrbitalScanViewProps> = ({ planetId, planetName, planetType }) => {
  const { pathD, dots } = useMemo(() => {
    const rand = seededRandom(planetId);
    const d = buildSilhouettePath(rand);
    const dotCount = 4 + Math.floor(rand() * 4); // 4-7 cosmetic candidate spots
    const spots = Array.from({ length: dotCount }, () => ({
      x: 30 + rand() * 140,
      y: 30 + rand() * 140,
    }));
    return { pathD: d, dots: spots };
  }, [planetId]);

  return (
    <div className="orbital-scan-view">
      <div className="orbital-scan-header">
        <span className="orbital-scan-title">Orbital Scan</span>
        {planetName && <span className="orbital-scan-planet">{planetName}</span>}
      </div>
      <svg
        className="orbital-scan-svg"
        viewBox="0 0 200 200"
        role="img"
        aria-label={`Orbital scan silhouette${planetType ? ` of a ${planetType} world` : ''}`}
      >
        <path className="orbital-scan-silhouette" d={pathD} />
        {dots.map((dot, i) => (
          <circle
            key={i}
            className="orbital-scan-dot"
            cx={dot.x}
            cy={dot.y}
            r={3}
            aria-hidden="true"
          />
        ))}
      </svg>
      <div className="orbital-scan-hint">
        Candidate landing markers are illustrative — launch an expedition to reveal an actual site.
      </div>
    </div>
  );
};

export default OrbitalScanView;
