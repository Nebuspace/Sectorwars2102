import React, { type RefObject } from 'react';
import type { SectorWreck } from '../../services/api';
import type { SpecialFormationSummary } from '../../contexts/GameContext';
import type { HitMeta } from './SolarSystemViewscreen';
import { scanPosition, type HazardArc, type PctPoint, type StarAnchor } from './windshieldTableauLayout';
import { arcPath, orbitEllipse, type TravelPhase } from './windshieldTableauHelpers';

/**
 * windshieldTableauChrome — WO-AAA-SOLAR-TABLEAU phase 3 module split.
 * Two self-contained, presentational-only render blocks extracted VERBATIM
 * out of WindshieldTableau.tsx's own `.scene.space` JSX (mechanical
 * extraction to bring that file back under the 1500-line TS cap) — every
 * value each block used to read off component state/props is now an
 * explicit prop instead. Neither owns any state; both are pure functions of
 * their props -> JSX, exactly matching WindshieldTableau.tsx's own render
 * output before the move.
 */

// ---------------------------------------------------------------------------
// HazardArcsLayer — nebula haze + collision-debris ring, blurred SVG arcs
// along the star's orbital plane (not rings).
// ---------------------------------------------------------------------------

export interface HazardArcsLayerProps {
  star: StarAnchor;
  hazeArcs: HazardArc[];
  debrisRingArc: HazardArc | null;
  nebula: { hue: number; density: number } | null;
  debris: { inner_au: number; outer_au: number; hue: number } | null;
}

export function HazardArcsLayer({ star, hazeArcs, debrisRingArc, nebula, debris }: HazardArcsLayerProps): React.ReactNode {
  if (hazeArcs.length === 0 && !debrisRingArc) return null;
  return (
    <svg className="hazard-arcs" viewBox="0 0 100 100" preserveAspectRatio="none">
      <defs>
        <filter id="ssv-hblur" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="1.1" />
        </filter>
      </defs>
      {hazeArcs.map((arc, i) => (
        <path
          key={`neb-${i}`}
          d={arcPath(star, arc)}
          stroke={`hsla(${nebula?.hue ?? 260}, 70%, 55%, ${Math.min(0.4, Math.max(0.1, (nebula?.density ?? 0.3) * 0.35))})`}
          strokeWidth={2.2}
          fill="none"
          strokeLinecap="round"
          filter="url(#ssv-hblur)"
        />
      ))}
      {debrisRingArc && debris && (
        <path
          key="debris"
          d={arcPath(star, debrisRingArc)}
          stroke={`hsla(${debris.hue}, 30%, 45%, 0.4)`}
          strokeWidth={1.6}
          fill="none"
          strokeLinecap="round"
          filter="url(#ssv-hblur)"
        />
      )}
    </svg>
  );
}

// ---------------------------------------------------------------------------
// ScanLayer — wrecks + formations, gated behind scanActive.
// ---------------------------------------------------------------------------

export interface ScanLayerProps {
  scanActive: boolean;
  wrecks: SectorWreck[];
  formations: SpecialFormationSummary[];
  star: StarAnchor;
  /** `openPopup` in the original closure. */
  onOpenPopup: (meta: HitMeta, name: string, pos: PctPoint, objectId?: string | null) => void;
}

export function ScanLayer({ scanActive, wrecks, formations, star, onOpenPopup }: ScanLayerProps): React.ReactNode {
  if (!scanActive) return null;
  return (
    <>
      {wrecks.map((w) => {
        const pos = scanPosition(w.id);
        return (
          <React.Fragment key={`wreck-${w.id}`}>
            {orbitEllipse(star, pos, `orbit-wreck-${w.id}`)}
            <button
              type="button"
              className="obj"
              style={{ left: `${pos.xPct}%`, top: `${pos.yPct}%`, transform: 'translate(-50%,-50%)', background: 'none', border: 'none' }}
              aria-label={`Wreckage — ${w.destroyed_ship_type}`}
              onClick={() =>
                onOpenPopup(
                  { kind: 'wreck', wreckId: w.id, shipType: w.destroyed_ship_type, cause: w.cause, suspect: w.would_flag_suspect },
                  'WRECKAGE',
                  pos
                )
              }
            >
              <svg viewBox="0 0 44 20" style={{ width: '1.9em', height: '.9em', display: 'block', transform: 'rotate(-11deg)', opacity: 0.5 }}>
                <path d="M4 11 L15 6 L19 9 L10 14 Z" fill="#4A4038" stroke="#8A7A66" strokeWidth={0.7} />
                <path d="M23 9 L34 4 L39 7 L28 13 Z" fill="#3E362E" stroke="#7A6A56" strokeWidth={0.7} transform="rotate(14 31 8)" />
                <line x1="17" y1="9" x2="24" y2="8" stroke="#5A4E42" strokeWidth={0.6} strokeDasharray="1.5 1.5" />
                <circle cx="14" cy="16" r={0.7} fill="#6E6254" />
                <circle cx="30" cy="16" r={0.5} fill="#6E6254" />
                <circle cx="38" cy="12" r={0.6} fill="#57493E" />
                <circle cx="21" cy="4" r={0.5} fill="#8A7A66" />
              </svg>
              <span className="objtag">WRECK — SALVAGE</span>
            </button>
          </React.Fragment>
        );
      })}
      {formations.map((f) => {
        const pos = scanPosition(f.id);
        const discovered = f.is_discovered;
        return discovered ? (
          <button
            key={`formation-${f.id}`}
            type="button"
            className="obj"
            style={{ left: `${pos.xPct}%`, top: `${pos.yPct}%`, transform: 'translate(-50%,-50%)' }}
            aria-label={f.name || 'Discovered anomaly'}
            onClick={() => onOpenPopup({ kind: 'formation', formationId: f.id, name: f.name, type: f.type, discovered: true }, f.name || 'ANOMALY', pos)}
          >
            <span className="glyphbox" style={{ color: '#C9B8F5' }}>◇</span>
            <span className="objtag">{(f.name || 'DERELICT BEACON').toUpperCase()}</span>
          </button>
        ) : (
          <button
            key={`formation-${f.id}`}
            type="button"
            className="anom"
            style={{ left: `${pos.xPct}%`, top: `${pos.yPct}%`, transform: 'translate(-50%,-50%)' }}
            aria-label="Unresolved signal"
            title="an unresolved flicker — fly to it"
            onClick={() => onOpenPopup({ kind: 'formation', formationId: f.id, name: null, type: null, discovered: false }, 'UNIDENTIFIED ANOMALY', pos)}
          >
            ◇
          </button>
        );
      })}
    </>
  );
}

// ---------------------------------------------------------------------------
// PlayerShipAndWarpLayer — the player's own ship marker (the ONLY system-
// level mover) + its RCS attitude jets + the warp cinematic (spherical field
// that inflates around the hull, snaps + streaks out, then a flash as the
// destination sector takes over). Purely decorative beyond the marker itself
// -- all driven off already-resolved state, no state of its own.
// ---------------------------------------------------------------------------

export type TableauWarpPhase = 'idle' | 'turning' | 'charging' | 'launch' | 'arriving';

export interface PlayerShipAndWarpLayerProps {
  shipPos: PctPoint | null;
  shipMkRef: RefObject<HTMLDivElement | null>;
  burning: boolean;
  travelPhase: TravelPhase;
  warpPhase: TableauWarpPhase;
  heading: number;
  warpBearing: number;
  arrivalBearing: number;
}

export function PlayerShipAndWarpLayer({
  shipPos, shipMkRef, burning, travelPhase, warpPhase, heading, warpBearing, arrivalBearing,
}: PlayerShipAndWarpLayerProps): React.ReactNode {
  if (!shipPos) return null;
  return (
    <>
      <div
        ref={shipMkRef}
        className={`shipmk${burning ? ' burning' : ''}${travelPhase !== 'idle' ? ` travel-${travelPhase}` : ''}${warpPhase === 'turning' ? ' warp-turning' : ''}${warpPhase === 'launch' ? ' warp-launching' : ''}${warpPhase === 'arriving' ? ' warp-arriving' : ''}`}
        style={{ left: `${shipPos.xPct}%`, top: `${shipPos.yPct}%`, '--hdg': `${heading.toFixed(0)}deg`, '--warp-bearing': `${warpBearing.toFixed(0)}deg`, '--arrival-bearing': `${arrivalBearing.toFixed(0)}deg` } as React.CSSProperties}
      >
        ➤
        {/* RCS attitude jets fire for every attitude change: initial local
            orientation, flip for braking, final facing, and pre-warp turn. */}
        {(warpPhase === 'turning' ||
          travelPhase === 'orienting' ||
          travelPhase === 'brake-turn' ||
          travelPhase === 'halt-turn' ||
          travelPhase === 'redirect-turn' ||
          travelPhase === 'final-orient') && (
          <>
            <span className="ssv-rcs ssv-rcs-a" aria-hidden="true" />
            <span className="ssv-rcs ssv-rcs-b" aria-hidden="true" />
          </>
        )}
      </div>

      {/* Warp cinematic — a spherical warp field that inflates around the
          hull (charging), snaps + streaks out along the exit bearing
          (launch), then a flash as the destination sector takes over
          (arriving). Anchored to the ship's live position. Purely decorative. */}
      {warpPhase !== 'idle' && warpPhase !== 'turning' && (
        <div
          className={`ssv-warp warp-${warpPhase}`}
          style={{ left: `${shipPos.xPct}%`, top: `${shipPos.yPct}%`, '--warp-bearing': `${warpBearing.toFixed(0)}deg`, '--arrival-bearing': `${arrivalBearing.toFixed(0)}deg` } as React.CSSProperties}
          aria-hidden="true"
        >
          <span className="ssv-warp-bubble" />
          <span className="ssv-warp-streak" />
        </div>
      )}
      {(warpPhase === 'launch' || warpPhase === 'arriving') && (
        <div className="ssv-warp-flash" aria-hidden="true" />
      )}
    </>
  );
}
