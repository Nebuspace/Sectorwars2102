import React from 'react';
import './citadel-structure.css';

/**
 * CitadelStructure — pure-SVG architectural silhouette of the citadel.
 *
 * All five strata are always rendered; CSS state classes decide how each
 * one appears:
 *   - lit          → level already built (below current level)
 *   - current      → the level you hold now (soft breathing glow)
 *   - ghost        → future level, dim dashed outline ("what you could become")
 *   - constructing → next level while an upgrade is in progress (scaffold shimmer)
 *
 * Strata (canonical names):
 *   L1 Outpost           — single habitat dome + antenna
 *   L2 Settlement        — clustered flanking domes + perimeter wall
 *   L3 Colony            — watchtowers on the wall + central comm spire
 *   L4 Major Colony      — orbital defense ring arc above the city
 *   L5 Planetary Capital — layered towers, twin orbital rings, crown beacon
 */

export type StratumState = 'lit' | 'current' | 'ghost' | 'constructing';

interface CitadelStructureProps {
  /** Current citadel level (0–5). */
  level: number;
  /** True while the citadel API reports an active upgrade. */
  isUpgrading?: boolean;
  /** The level under construction while upgrading (defaults to level + 1). */
  upgradingToLevel?: number | null;
}

const stratumState = (
  stratum: number,
  level: number,
  isUpgrading: boolean,
  upgradingTo: number
): StratumState => {
  if (isUpgrading && stratum === upgradingTo) return 'constructing';
  if (stratum < level) return 'lit';
  if (stratum === level) return 'current';
  return 'ghost';
};

const CitadelStructure: React.FC<CitadelStructureProps> = ({
  level,
  isUpgrading = false,
  upgradingToLevel = null,
}) => {
  const upgradingTo = upgradingToLevel ?? level + 1;
  const cls = (stratum: number): string =>
    `citadel-stratum state-${stratumState(stratum, level, isUpgrading, upgradingTo)}`;

  return (
    <div className="citadel-structure" aria-hidden="false">
      <svg
        viewBox="0 0 360 200"
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label={`Citadel structure visualization, level ${level} of 5${isUpgrading ? `, upgrading to level ${upgradingTo}` : ''}`}
        className="citadel-structure-svg"
      >
        <defs>
          <linearGradient id="cs-beam" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#00ffcc" stopOpacity="0" />
            <stop offset="100%" stopColor="#00ffcc" stopOpacity="0.55" />
          </linearGradient>
          <linearGradient id="cs-dome" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#4a9eff" stopOpacity="0.28" />
            <stop offset="100%" stopColor="#4a9eff" stopOpacity="0.05" />
          </linearGradient>
        </defs>

        {/* Ground line — always visible */}
        <line className="cs-ground" x1="14" y1="170" x2="346" y2="170" />

        {/* ===== L5 Planetary Capital — layered towers + twin orbital rings + crown beacon ===== */}
        {/* Rendered before lower strata so the skyline towers sit behind the domes. */}
        <g className={`${cls(5)} stratum-l5`} data-stratum="5">
          {/* Crown beacon — light column above the comm spire */}
          <polygon className="cs-beacon-beam" points="176,76 184,76 188,14 172,14" fill="url(#cs-beam)" stroke="none" />
          <circle className="cs-light cs-beacon-light" cx="180" cy="74" r="3.4" />
          {/* Second (upper) orbital ring of the twin pair */}
          <path className="cs-ring" d="M 58 78 Q 180 18 302 78" fill="none" />
          <circle className="cs-ring-node" cx="120" cy="55" r="2.2" />
          <circle className="cs-ring-node" cx="240" cy="55" r="2.2" />
          {/* Layered tower skyline */}
          <rect className="cs-tower" x="142" y="108" width="12" height="62" />
          <rect className="cs-tower" x="156" y="92" width="13" height="78" />
          <rect className="cs-tower" x="191" y="98" width="13" height="72" />
          <rect className="cs-tower" x="206" y="116" width="11" height="54" />
          {/* Lit windows on the towers */}
          <line className="cs-window" x1="148" y1="118" x2="148" y2="160" />
          <line className="cs-window" x1="162.5" y1="102" x2="162.5" y2="160" />
          <line className="cs-window" x1="197.5" y1="108" x2="197.5" y2="160" />
          <line className="cs-window" x1="211.5" y1="126" x2="211.5" y2="160" />
        </g>

        {/* ===== L4 Major Colony — orbital defense ring arc ===== */}
        <g className={`${cls(4)} stratum-l4`} data-stratum="4">
          <path className="cs-ring" d="M 50 100 Q 180 40 310 100" fill="none" />
          <circle className="cs-ring-node" cx="115" cy="77.5" r="2.6" />
          <circle className="cs-ring-node" cx="180" cy="70" r="3" />
          <circle className="cs-ring-node" cx="245" cy="77.5" r="2.6" />
        </g>

        {/* ===== L3 Colony — watchtowers + comm spire ===== */}
        <g className={`${cls(3)} stratum-l3`} data-stratum="3">
          {/* Left watchtower */}
          <rect className="cs-tower" x="56" y="134" width="12" height="36" />
          <rect className="cs-tower-cap" x="53" y="128" width="18" height="6" />
          {/* Right watchtower */}
          <rect className="cs-tower" x="292" y="134" width="12" height="36" />
          <rect className="cs-tower-cap" x="289" y="128" width="18" height="6" />
          {/* Central comm spire rising from the habitat dome */}
          <line className="cs-mast" x1="180" y1="136" x2="180" y2="78" />
          <line className="cs-mast-strut" x1="172" y1="122" x2="188" y2="122" />
          <line className="cs-mast-strut" x1="174" y1="104" x2="186" y2="104" />
          <path className="cs-dish" d="M 180 92 Q 191 86 193 78" fill="none" />
          <circle className="cs-light" cx="180" cy="76" r="2.2" />
        </g>

        {/* ===== L2 Settlement — clustered flanking domes + perimeter wall ===== */}
        <g className={`${cls(2)} stratum-l2`} data-stratum="2">
          {/* Flanking habitat domes */}
          <path className="cs-dome" d="M 100 170 A 21 21 0 0 1 142 170 Z" fill="url(#cs-dome)" />
          <path className="cs-dome" d="M 218 170 A 21 21 0 0 1 260 170 Z" fill="url(#cs-dome)" />
          {/* Perimeter wall segments with posts */}
          <line className="cs-wall" x1="62" y1="162" x2="98" y2="162" />
          <line className="cs-wall" x1="262" y1="162" x2="298" y2="162" />
          <line className="cs-wall-post" x1="62" y1="162" x2="62" y2="170" />
          <line className="cs-wall-post" x1="80" y1="162" x2="80" y2="170" />
          <line className="cs-wall-post" x1="98" y1="162" x2="98" y2="170" />
          <line className="cs-wall-post" x1="262" y1="162" x2="262" y2="170" />
          <line className="cs-wall-post" x1="280" y1="162" x2="280" y2="170" />
          <line className="cs-wall-post" x1="298" y1="162" x2="298" y2="170" />
        </g>

        {/* ===== L1 Outpost — single habitat dome + antenna ===== */}
        <g className={`${cls(1)} stratum-l1`} data-stratum="1">
          <path className="cs-dome cs-dome-main" d="M 146 170 A 34 34 0 0 1 214 170 Z" fill="url(#cs-dome)" />
          <line className="cs-mast" x1="206" y1="150" x2="218" y2="114" />
          <line className="cs-mast-strut" x1="209" y1="136" x2="217" y2="139" />
          <circle className="cs-light" cx="219" cy="111" r="2.4" />
        </g>
      </svg>
    </div>
  );
};

export default CitadelStructure;
