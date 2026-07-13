import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import apiClient from '../../services/apiClient';
import { useAutopilot } from '../../contexts/AutopilotContext';
import type { SectorWreck } from '../../services/api';
import type { SpecialFormationSummary } from '../../contexts/GameContext';
import type {
  HitMeta,
  ShipPresence,
  SystemBody,
  SystemStation,
} from './SolarSystemViewscreen';
import { shipFaction } from './SolarSystemViewscreen';
import {
  AU_SEMI_X_PCT,
  AU_SEMI_Y_PCT,
  beltStyle,
  bodyPosition,
  debrisArc,
  decorativeRings,
  headingDeg,
  moonOrbits,
  nebulaArcs,
  otherPresencePosition,
  scanPosition,
  selfRestingAnchor,
  starAnchor,
  stationPosition,
  type HazardArc,
  type PctPoint,
  type StarAnchor,
} from './windshieldTableauLayout';
import './solar-system-viewscreen.css';

/**
 * WindshieldTableau — the flight-mode windshield-band scene
 * (WO-UI2-WINDSHIELD-TABLEAU), replacing SolarSystemViewscreen's canvas
 * orrery with the ratified demo's STATIC DOM "sliver" composition (Max,
 * live-playtest #4: "a sliver of the solar system with all objects in it,
 * no rotating around the sun").
 *
 * SolarSystemViewscreen.tsx is intentionally left byte-for-byte untouched
 * for its 'flight' scene path (this WO stops MOUNTING it there, in
 * GameDashboard.tsx, rather than editing its canvas/orbital-closeup/popup
 * code) — see this component's own file-header verify-first note below for
 * why. It still owns 'docked' and 'landed' scenes unchanged, and CHART
 * 2D/3D (NavigationMap/Galaxy3DRenderer) is a wholly separate component,
 * also untouched.
 *
 * VERIFY-FIRST FINDING (orbital closeup): the WO's brief asked to leave
 * "orbital closeup" alone as a co-existing canvas painter, believing it was
 * a separate mount (like CHART). It is not — SolarSystemViewscreen.tsx's
 * `enterOrbit`/`drawOrbitCloseup` only ever triggers from a click inside the
 * SAME 'flight' canvas this WO replaces, via `handleClick`'s
 * `target.kind === 'planet'` branch (SolarSystemViewscreen.tsx, "Clicking a
 * planet zooms the windshield to an orbital closeup of it"). Since that
 * canvas is no longer mounted for flight, closeup becomes unreachable dead
 * code (harmless — the file is untouched, so nothing breaks; it simply has
 * no live entry point anymore). This tableau instead reuses the OTHER path
 * that file's own comment calls out as the deliberate LAND fallback:
 * "clicking a real planet now enters the orbital closeup... this popup
 * branch is a fallback only — kept for the LAND action if a planet popup is
 * ever opened by another path." That "another path" is this component's
 * click→popup→LAND flow (ssv-popup, reused verbatim) — the demo's own
 * idiom is exactly this simpler click-to-inspect model, not a full-screen
 * zoom.
 *
 * DATA: fetches GET /api/v1/sectors/{id}/contents (WO-UI2-INTRASYSTEM-MODEL,
 * ec21a3eb) once per sectorId change for the STATIC celestial composition
 * (star/bodies/stations/nebula/belt/debris/habitable_zone — the same fields
 * SolarSystemViewscreen.tsx's own GET /sectors/{id}/system already served,
 * unioned into the one consolidated read-only endpoint the backend shipped
 * specifically anticipating this FE pass). Live, WS-reactive data
 * (ships/wrecks/formations) stays on PROPS from GameDashboard exactly as
 * today, deliberately — /contents is a plain poll-once GET with no WS
 * push, and switching those three feeds to it would trade away the
 * liveness GameDashboard's currentSector context already provides for no
 * WO-required benefit.
 */

// ---------------------------------------------------------------------------
// Contract subset (mirrors SectorContentsResponse's static fields — see
// services/gameserver/src/api/routes/sectors.py's get_sector_contents).
// ---------------------------------------------------------------------------

interface StaticSystem {
  star: { kind: string; label: string; color: string } | null;
  nebula: { hue: number; density: number } | null;
  belt: { inner_au: number; outer_au: number } | null;
  debris: { inner_au: number; outer_au: number; hue: number } | null;
  bodies: SystemBody[];
  stations: SystemStation[];
}

export interface WindshieldTableauProps {
  sectorId: number;
  /** Cosmetic-only: tints the scene background when the sector is
   *  dangerous (demo's `sec.hazard>=5` → `.scene.space.hazard`). The
   *  Annunciator/locrow own the actual hazard READOUT — this is background
   *  chrome only. */
  hazardLevel?: number;
  /** Real DB planet records (owner_name etc.) — /contents' bodies carry
   *  `owned` but not `owner_name`; this stays a prop exactly as
   *  SolarSystemViewscreen.tsx already receives it. */
  planets?: Array<{ id: string; owner_name?: string | null; owner_id?: string | null }>;
  ships?: ShipPresence[];
  wrecks?: SectorWreck[];
  formations?: SpecialFormationSummary[];
  scanActive?: boolean;
  onRequestLand?: (planetId: string) => void;
  onRequestDock?: (stationId: string) => void;
  selectedShipId?: string | null;
  onSelectShip?: (id: string) => void;
  /** Max refinement (5b): "undock emerges at the host's position" — the
   *  station/planet id the player just left, so the ship's FIRST frame in
   *  this fresh mount starts there instead of a generic seeded anchor.
   *  GameDashboard tracks these via a ref that survives the docked/landed
   *  unmount boundary (this component itself remounts on every
   *  dock↔flight/land↔flight transition, per the existing conditional
   *  mount structure — see GameDashboard.tsx). */
  lastDockedStationId?: string | null;
  lastLandedPlanetId?: string | null;
}

const POPUP_W = 232;
const POPUP_H = 158;

interface PopupState {
  key: string;
  meta: HitMeta;
  name: string;
  xPct: number;
  yPct: number;
}

function arcPath(star: StarAnchor, arc: HazardArc): string {
  const rx = arc.rFrac * AU_SEMI_X_PCT;
  const ry = arc.rFrac * AU_SEMI_Y_PCT;
  const startRad = (arc.startDeg * Math.PI) / 180;
  const endRad = ((arc.startDeg + arc.sweepDeg) * Math.PI) / 180;
  const sx = (star.xPct + Math.cos(startRad) * rx).toFixed(2);
  const sy = (star.yPct + Math.sin(startRad) * ry).toFixed(2);
  const ex = (star.xPct + Math.cos(endRad) * rx).toFixed(2);
  const ey = (star.yPct + Math.sin(endRad) * ry).toFixed(2);
  const largeArc = arc.sweepDeg > 180 ? 1 : 0;
  return `M ${sx} ${sy} A ${rx.toFixed(2)} ${ry.toFixed(2)} 0 ${largeArc} 1 ${ex} ${ey}`;
}

const WindshieldTableau: React.FC<WindshieldTableauProps> = ({
  sectorId,
  hazardLevel = 0,
  planets = [],
  ships = [],
  wrecks = [],
  formations = [],
  scanActive = false,
  onRequestLand,
  onRequestDock,
  selectedShipId = null,
  onSelectShip,
  lastDockedStationId = null,
  lastLandedPlanetId = null,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [system, setSystem] = useState<StaticSystem | null>(null);
  const [fetchFailed, setFetchFailed] = useState(false);
  const [popup, setPopup] = useState<PopupState | null>(null);

  useEffect(() => {
    let cancelled = false;
    setSystem(null);
    setFetchFailed(false);
    setPopup(null);
    apiClient
      .get(`/api/v1/sectors/${sectorId}/contents`)
      .then((res) => {
        if (cancelled) return;
        const d = res.data || {};
        setSystem({
          star: d.star ?? null,
          nebula: d.nebula ?? null,
          belt: d.belt ?? null,
          debris: d.debris ?? null,
          bodies: Array.isArray(d.bodies) ? d.bodies : [],
          stations: Array.isArray(d.stations) ? d.stations : [],
        });
      })
      .catch((err) => {
        if (cancelled) return;
        // eslint-disable-next-line no-console
        console.error('WindshieldTableau: sector contents fetch failed:', err);
        setFetchFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [sectorId]);

  const star = useMemo(() => starAnchor(sectorId, system?.star ?? null), [sectorId, system?.star]);
  const rings = useMemo(() => decorativeRings(star), [star]);
  const belt = useMemo(() => (system?.belt ? beltStyle(star) : null), [star, system?.belt]);
  const hazeArcs = useMemo(() => (system?.nebula ? nebulaArcs(sectorId) : []), [sectorId, system?.nebula]);
  const debrisRingArc = useMemo(() => (system?.debris ? debrisArc(system.debris) : null), [system?.debris]);

  // ---- Player's own ship marker — the ONLY system-level mover. ----
  const [shipPos, setShipPos] = useState<PctPoint | null>(null);
  const [heading, setHeading] = useState(0);
  const [localBurn, setLocalBurn] = useState(false);
  const shipPosRef = useRef<PctPoint | null>(null);
  shipPosRef.current = shipPos;
  const seededSectorRef = useRef<number | null>(null);
  const autopilot = useAutopilot();

  useEffect(() => {
    if (!system) return; // wait for the fetch that resolves dock/land host lookups
    if (seededSectorRef.current === sectorId) return;
    seededSectorRef.current = sectorId;
    let anchor: PctPoint | null = null;
    if (lastDockedStationId) {
      const st = system.stations.find((s) => s.station_id === lastDockedStationId);
      if (st) anchor = stationPosition(star, st);
    }
    if (!anchor && lastLandedPlanetId) {
      const b = system.bodies.find((bb) => bb.planet_id === lastLandedPlanetId);
      if (b) anchor = bodyPosition(star, b);
    }
    if (!anchor) anchor = selfRestingAnchor(sectorId);
    setShipPos(anchor);
    setHeading(0);
  }, [system, sectorId, lastDockedStationId, lastLandedPlanetId, star]);

  const travelTo = useCallback((target: PctPoint) => {
    const from = shipPosRef.current ?? target;
    setHeading(headingDeg(from, target));
    setShipPos(target);
    setLocalBurn(true);
  }, []);

  const burning = localBurn || autopilot.status === 'engaged';

  // ---- Popups (click → info card, reusing the .ssv-popup glass) ----
  const openPopup = useCallback((meta: HitMeta, name: string, pos: PctPoint) => {
    setPopup({ key: `${meta.kind}:${name}`, meta, name, xPct: pos.xPct, yPct: pos.yPct });
    travelTo(pos);
  }, [travelTo]);

  const popupStyle = useMemo((): React.CSSProperties | null => {
    if (!popup || !containerRef.current) return { left: 8, top: 8 };
    const rect = containerRef.current.getBoundingClientRect();
    const px = (popup.xPct / 100) * rect.width;
    const py = (popup.yPct / 100) * rect.height;
    const left = Math.min(Math.max(6, px + 14), Math.max(6, rect.width - POPUP_W - 6));
    const top = Math.min(Math.max(6, py - POPUP_H / 2), Math.max(6, rect.height - POPUP_H - 6));
    return { left, top };
  }, [popup]);

  const renderPopupContent = (): React.ReactNode => {
    if (!popup) return null;
    const meta = popup.meta;
    switch (meta.kind) {
      case 'star':
        return (
          <>
            <div className="ssv-popup-title">{meta.label.toUpperCase()}</div>
            <div className="ssv-popup-line">
              <span className="ssv-popup-swatch" style={{ background: meta.color }} aria-hidden="true"></span>
              CLASS {meta.starClass}
            </div>
            <div className="ssv-popup-line">PRIMARY — SECTOR {sectorId}</div>
          </>
        );
      case 'procedural':
        return (
          <>
            <div className="ssv-popup-title proc">{meta.designation}</div>
            <div className="ssv-popup-line proc">{meta.typeName}</div>
            <div className="ssv-popup-line proc">{meta.sizeDesc}</div>
            <div className="ssv-popup-status">UNSURVEYED — NO LANDING SITE</div>
          </>
        );
      case 'planet': {
        const ownerName = meta.owned
          ? planets.find((p) => p.id === meta.planetId)?.owner_name || 'CLAIMED'
          : null;
        return (
          <>
            <div className="ssv-popup-title">{popup.name.toUpperCase()}</div>
            <div className="ssv-popup-line">{meta.planetKind.replace(/_/g, ' ').toUpperCase()}</div>
            {typeof meta.habitability === 'number' && (
              <div className="ssv-popup-line">HABITABILITY {Math.round(meta.habitability)}%</div>
            )}
            {ownerName && <div className="ssv-popup-line">OWNER — {ownerName}</div>}
            {onRequestLand && (
              <button
                type="button"
                className="ssv-popup-action"
                onClick={() => { setPopup(null); onRequestLand(meta.planetId); }}
              >
                🛬 LAND
              </button>
            )}
          </>
        );
      }
      case 'station':
        return (
          <>
            <div className="ssv-popup-title">{popup.name.toUpperCase()}</div>
            <div className="ssv-popup-line">{meta.stationType.replace(/_/g, ' ').toUpperCase()}</div>
            {onRequestDock && (
              <button
                type="button"
                className="ssv-popup-action"
                onClick={() => { setPopup(null); onRequestDock(meta.stationId); }}
              >
                ⚓ DOCK
              </button>
            )}
          </>
        );
      case 'ship':
        return (
          <>
            <div className="ssv-popup-title">{meta.shipName.toUpperCase()}</div>
            <div className="ssv-popup-line">
              <span className="ssv-popup-swatch" style={{ background: meta.factionColor }} aria-hidden="true"></span>
              {meta.factionLabel}
            </div>
            <div className="ssv-popup-line">{meta.shipType.replace(/_/g, ' ').toUpperCase()}</div>
            <div className="ssv-popup-line">{meta.isNpc ? 'NPC' : 'PILOT'} — {meta.captain.toUpperCase()}</div>
            {meta.isNpc && (
              <div className="ssv-popup-status" style={{ color: meta.lawful ? '#ffb000' : '#00ff41' }}>
                {meta.lawful ? '⚑ LAWFUL TARGET' : '✋ PROTECTED — ATTACK IS A CRIME'}
              </div>
            )}
          </>
        );
      case 'wreck':
        return (
          <>
            <div className="ssv-popup-title proc">WRECKAGE</div>
            <div className="ssv-popup-line proc">{meta.shipType.replace(/_/g, ' ').toUpperCase()}</div>
            <div className="ssv-popup-line proc">CAUSE — {meta.cause.replace(/_/g, ' ').toUpperCase()}</div>
            <div className="ssv-popup-status">{meta.suspect ? 'SALVAGE FLAGGED — CAUTION' : 'UNCLAIMED SALVAGE'}</div>
          </>
        );
      case 'formation':
        return (
          <>
            <div className="ssv-popup-title">{(meta.name || 'UNIDENTIFIED ANOMALY').toUpperCase()}</div>
            <div className="ssv-popup-line">{(meta.type || 'FORMATION').replace(/_/g, ' ').toUpperCase()}</div>
            <div className="ssv-popup-status">{meta.discovered ? 'DISCOVERED' : 'UNDISCOVERED — SCAN TO CONFIRM'}</div>
          </>
        );
      default:
        return null;
    }
  };

  if (fetchFailed) {
    return (
      <div ref={containerRef} className="ssv-tableau">
        <div className="scene space">
          <div className="stars" />
          <div style={{
            position: 'absolute', left: '50%', top: '50%', transform: 'translate(-50%,-50%)',
            color: 'rgba(0,217,255,0.32)', fontSize: '0.75em', letterSpacing: '.06em',
          }}>
            SCAN ACQUISITION FAILED
          </div>
        </div>
      </div>
    );
  }

  const hasNebula = !!system?.nebula;
  const hasHazard = hazardLevel >= 5;
  const selectedShip = ships.find((s) => s.ship_id && String(s.ship_id) === String(selectedShipId));
  const selectedPos = selectedShip ? otherPresencePosition(String(selectedShip.ship_id)) : null;

  return (
    <div ref={containerRef} className="ssv-tableau">
      <div className={`scene space${hasNebula ? ' nebula' : ''}${hasHazard ? ' hazard' : ''}`}>
        <div className="stars" />

        {/* hazard bands — nebula haze + collision-debris ring, blurred SVG
            arcs along the star's orbital plane (not rings). */}
        {(hazeArcs.length > 0 || debrisRingArc) && (
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
                stroke={`hsla(${system?.nebula?.hue ?? 260}, 70%, 55%, ${Math.min(0.4, Math.max(0.1, (system?.nebula?.density ?? 0.3) * 0.35))})`}
                strokeWidth={2.2}
                fill="none"
                strokeLinecap="round"
                filter="url(#ssv-hblur)"
              />
            ))}
            {debrisRingArc && system?.debris && (
              <path
                key="debris"
                d={arcPath(star, debrisRingArc)}
                stroke={`hsla(${system.debris.hue}, 30%, 45%, 0.4)`}
                strokeWidth={1.6}
                fill="none"
                strokeLinecap="round"
                filter="url(#ssv-hblur)"
              />
            )}
          </svg>
        )}

        {/* decorative orbit rings — flat, never tied to a real body */}
        {rings.map((r, i) => (
          <div
            key={`ring-${i}`}
            className="orbit"
            style={{
              left: `${r.xPct}%`, top: `${r.yPct}%`,
              width: `${r.wPct}%`, height: `${r.hPct}%`,
              transform: 'translate(-50%,-50%)',
            }}
          />
        ))}

        {/* asteroid belt — decorative, mostly off-frame (the "sliver") */}
        {belt && (
          <div
            className="belt"
            style={{
              left: `${belt.xPct}%`, top: `${belt.yPct}%`,
              width: `${belt.wPct}%`, height: `${belt.hPct}%`,
              transform: 'translate(-50%,-50%)',
            }}
          />
        )}

        {/* the star */}
        {system?.star && (
          <>
            <button
              type="button"
              className="sun"
              style={{
                left: `${star.xPct}%`, top: `${star.yPct}%`,
                width: `${star.sizeEm}em`, height: `${star.sizeEm}em`,
                transform: 'translate(-50%,-50%)',
                background: `radial-gradient(circle at 38% 35%, #FFFFFF, ${system.star.color} 45%, transparent 78%)`,
                boxShadow: `0 0 3em ${system.star.color}66, 0 0 1em ${system.star.color}`,
              }}
              onClick={() =>
                system.star &&
                openPopup(
                  { kind: 'star', label: system.star.label, starClass: system.star.kind.replace(/_/g, ' '), color: system.star.color },
                  system.star.label || 'PRIMARY STAR',
                  star
                )
              }
              aria-label={system.star.label || 'Primary star'}
            />
            <div className="pltag" style={{ position: 'absolute', left: `${star.xPct}%`, top: `${star.yPct + 14}%`, transform: 'translateX(-50%)' }}>
              {system.star.kind.replace(/_/g, ' ')}
            </div>
          </>
        )}

        {/* planets + their moons */}
        {(system?.bodies ?? []).map((body, idx) => {
          const pos = bodyPosition(star, body);
          const sizeEm = Math.min(2.4, Math.max(0.9, 0.55 + body.size_class * 0.28));
          const moons = moonOrbits(sectorId, body);
          const isReal = body.real && body.planet_id;
          const name = body.name || `slot-${body.slot}`;
          const label = isReal ? name : `PROCEDURAL-${sectorId}-${idx}`;
          return (
            <button
              key={`body-${body.slot}`}
              type="button"
              className="pl"
              style={{
                left: `${pos.xPct}%`, top: `${pos.yPct}%`,
                width: `${sizeEm}em`, height: `${sizeEm}em`,
                background: `hsl(${body.palette.hue}, ${body.palette.sat}%, 45%)`,
              }}
              aria-label={label}
              onClick={() =>
                isReal
                  ? openPopup(
                      { kind: 'planet', planetId: body.planet_id as string, planetKind: body.kind, habitability: body.habitability, owned: body.owned },
                      name,
                      pos
                    )
                  : openPopup(
                      { kind: 'procedural', designation: label, typeName: body.kind.replace(/_/g, ' '), sizeDesc: `SIZE CLASS ${body.size_class}` },
                      label,
                      pos
                    )
              }
            >
              <span className={`pltag${isReal && body.habitability ? '' : ' dim'}`}>
                {isReal ? name : label}{isReal && !body.habitability ? ' ◦' : ''}
              </span>
              {moons.map((m, mi) => (
                <span
                  key={`moon-${mi}`}
                  className={`moon-orbit${m.clockwise ? '' : ' ccw'}`}
                  style={{
                    animationDuration: `${m.durationS}s`,
                    // Negative delay = the standard CSS trick for a seeded
                    // starting phase on a looping animation without a jump
                    // discontinuity at each loop restart (an inline
                    // `transform` would fight the keyframe's own `from`).
                    animationDelay: `${-(m.startDeg / 360) * m.durationS}s`,
                  }}
                  aria-hidden="true"
                >
                  <span className="moon-dot" style={{ left: `${m.radiusEm}em`, top: 0 }} />
                </span>
              ))}
            </button>
          );
        })}

        {/* stations */}
        {(system?.stations ?? []).map((st) => {
          const pos = stationPosition(star, st);
          return (
            <button
              key={`station-${st.station_id}`}
              type="button"
              className="obj"
              style={{ left: `${pos.xPct}%`, top: `${pos.yPct}%`, transform: 'translate(-50%,-50%)' }}
              aria-label={st.name}
              onClick={() => openPopup({ kind: 'station', stationId: st.station_id, stationType: st.type }, st.name, pos)}
            >
              <span className="glyphbox">🛰</span>
              <span className="objtag">{st.name}</span>
            </button>
          );
        })}

        {/* SCAN layer — wrecks + formations, gated behind scanActive */}
        {scanActive && wrecks.map((w) => {
          const pos = scanPosition(w.id);
          return (
            <button
              key={`wreck-${w.id}`}
              type="button"
              className="obj"
              style={{ left: `${pos.xPct}%`, top: `${pos.yPct}%`, transform: 'translate(-50%,-50%)', background: 'none', border: 'none' }}
              aria-label={`Wreckage — ${w.destroyed_ship_type}`}
              onClick={() =>
                openPopup(
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
          );
        })}
        {scanActive && formations.map((f) => {
          const pos = scanPosition(f.id);
          const discovered = f.is_discovered;
          return discovered ? (
            <button
              key={`formation-${f.id}`}
              type="button"
              className="obj"
              style={{ left: `${pos.xPct}%`, top: `${pos.yPct}%`, transform: 'translate(-50%,-50%)' }}
              aria-label={f.name || 'Discovered anomaly'}
              onClick={() => openPopup({ kind: 'formation', formationId: f.id, name: f.name, type: f.type, discovered: true }, f.name || 'ANOMALY', pos)}
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
              onClick={() => openPopup({ kind: 'formation', formationId: f.id, name: null, type: null, discovered: false }, 'UNIDENTIFIED ANOMALY', pos)}
            >
              ◇
            </button>
          );
        })}

        {/* other ships/pirates — static seeded presence markers */}
        {ships.map((s) => {
          if (!s.ship_id) return null;
          const pos = otherPresencePosition(String(s.ship_id));
          const faction = shipFaction(s);
          const isPirate = faction.key === 'raider';
          return (
            <button
              key={`ship-${s.ship_id}`}
              type="button"
              className="other"
              style={{ left: `${pos.xPct}%`, top: `${pos.yPct}%`, color: faction.color }}
              aria-label={`${s.ship_name || 'Contact'} options`}
              onClick={() =>
                openPopup(
                  {
                    kind: 'ship', shipId: String(s.ship_id), shipName: s.ship_name || 'UNKNOWN',
                    shipType: s.ship_type || 'UNKNOWN', captain: s.username || 'UNKNOWN',
                    isNpc: !!s.is_npc, factionLabel: faction.label, factionColor: faction.color,
                    lawful: faction.lawful, notoriety: s.notoriety ?? undefined,
                  },
                  s.ship_name || 'Contact',
                  pos
                )
              }
            >
              {isPirate ? '☠' : '⊳'}
              <span className="pltag" style={{ color: faction.color }}>{s.ship_name || faction.label}</span>
            </button>
          );
        })}

        {/* target reticle */}
        {selectedPos && <div className="reticle" style={{ left: `${selectedPos.xPct}%`, top: `${selectedPos.yPct}%` }} />}

        {/* the player's own ship — the ONLY system-level mover */}
        {shipPos && (
          <div
            className={`shipmk${burning ? ' burning' : ''}`}
            style={{ left: `${shipPos.xPct}%`, top: `${shipPos.yPct}%`, '--hdg': `${heading.toFixed(0)}deg` } as React.CSSProperties}
            onTransitionEnd={() => setLocalBurn(false)}
          >
            ➤
          </div>
        )}
      </div>

      {popup && popupStyle && (
        <div className="ssv-popup" style={popupStyle} role="dialog" aria-label={`${popup.name} details`}>
          <button type="button" className="ssv-popup-close" onClick={() => setPopup(null)} aria-label="Close details">✕</button>
          {renderPopupContent()}
        </div>
      )}
    </div>
  );
};

export default WindshieldTableau;
