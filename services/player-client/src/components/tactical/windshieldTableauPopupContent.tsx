import React, { useState } from 'react';
import type { SystemStation } from './SolarSystemViewscreen';
import { beaconAPI } from '../../services/api';
import { bodyPosition, stationPosition, type PctPoint, type SafeOrbitRadii, type StarAnchor } from './windshieldTableauLayout';
import { DOCK_RANGE_EM, REFERENCE_BAND, distancePx, type PopupState, type StaticSystem } from './windshieldTableauHelpers';

/**
 * windshieldTableauPopupContent — WO-AAA-SOLAR-TABLEAU phase 3 module split.
 * WindshieldTableau.tsx's former `renderPopupContent` closure, extracted
 * VERBATIM (mechanical extraction to bring that file back under the
 * 1500-line TS cap) — every value it used to read off component state/props
 * is now an explicit param instead, so this is a pure function of its
 * arguments -> JSX with zero closure over the component. Behavior,
 * including every gate (withinLandRange/withinDockRange, approachingThis*),
 * is byte-identical to the pre-extraction closure.
 */
export interface TableauPopupContentParams {
  /** Non-null — the caller only invokes this once its own `popup &&` guard
   *  has already narrowed it (matches the original closure's own `if
   *  (!popup) return null;` early-out, now enforced by the caller instead). */
  popup: PopupState;
  sectorId: number;
  planets: Array<{ id: string; owner_name?: string | null; owner_id?: string | null }>;
  system: StaticSystem | null;
  star: StarAnchor;
  safeRadiiPlanets?: SafeOrbitRadii;
  safeRadiiStations?: SafeOrbitRadii;
  shipPos: PctPoint | null;
  localTraveling: boolean;
  glideTargetId: string | null;
  /** `flight.allStop()` in the original closure. */
  onHaltApproach: () => void;
  onRequestLand?: (planetId: string) => void;
  onRequestDock?: (stationId: string) => void;
  /** `setPopup(null)` in the original closure. */
  onClosePopup: () => void;
  /** `travelTo(pos, objectId)` in the original closure. */
  onApproachPlanet: (pos: PctPoint, planetId: string) => void;
  /** `approachStation(station, stationPos)` in the original closure. */
  onApproachStation: (station: SystemStation, pos: PctPoint) => void;
  /** Drop a sector-view beacon icon after salvage or read-once read. */
  onBeaconRemoved?: (beaconId: string) => void;
}

export function renderTableauPopupContent(params: TableauPopupContentParams): React.ReactNode {
  const {
    popup, sectorId, planets, system, star, safeRadiiPlanets, safeRadiiStations,
    shipPos, localTraveling, glideTargetId, onHaltApproach,
    onRequestLand, onRequestDock, onClosePopup, onApproachPlanet, onApproachStation,
    onBeaconRemoved,
  } = params;
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
      const body = system?.bodies.find((b) => b.planet_id === meta.planetId);
      const planetPos = body
        ? bodyPosition(star, body, safeRadiiPlanets)
        : { xPct: popup.xPct, yPct: popup.yPct };
      // canonical-%-space: REFERENCE_BAND, not bandBox -- this gate decides
      // whether the LAND button (-> onRequestLand -> POST /planets/land)
      // even appears. The server independently re-checks the SAME
      // proximity at the SAME fixed reference band before honoring the
      // request (intrasystem_movement_service.py's
      // DOCK_LAND_PROXIMITY_RANGE_EM / is_within_dock_land_range, whose own
      // comment says it's set to match this client gate "verbatim" so
      // server enforcement stays invisible to a legit player) -- gating on
      // a live-measured bandBox instead would show/hide LAND based on the
      // viewer's screen size rather than the server's actual ruling.
      const withinLandRange = Boolean(
        shipPos &&
        distancePx(shipPos, planetPos, REFERENCE_BAND) <= DOCK_RANGE_EM * REFERENCE_BAND.remPx
      );
      const approachingThisPlanet = localTraveling && glideTargetId === meta.planetId;
      return (
        <>
          <div className="ssv-popup-title">{popup.name.toUpperCase()}</div>
          <div className="ssv-popup-line">{meta.planetKind.replace(/_/g, ' ').toUpperCase()}</div>
          {typeof meta.habitability === 'number' && (
            <div className="ssv-popup-line">HABITABILITY {Math.round(meta.habitability)}%</div>
          )}
          {ownerName && <div className="ssv-popup-line">OWNER — {ownerName}</div>}
          {approachingThisPlanet ? (
            <button
              type="button"
              className="ssv-popup-action halt"
              onClick={() => onHaltApproach()}
              aria-label={`Halt approach to ${popup.name}`}
            >
              🛑 HALT
            </button>
          ) : withinLandRange && onRequestLand ? (
            <button
              type="button"
              className="ssv-popup-action"
              onClick={() => { onClosePopup(); onRequestLand(meta.planetId); }}
            >
              🛬 LAND
            </button>
          ) : body ? (
            <button
              type="button"
              className="ssv-popup-action"
              onClick={() => onApproachPlanet(planetPos, meta.planetId)}
            >
              ➤ APPROACH
            </button>
          ) : null}
          {!withinLandRange && !approachingThisPlanet && (
            <div className="ssv-popup-status">OUTSIDE LANDING RANGE</div>
          )}
        </>
      );
    }
    case 'station': {
      const station = system?.stations.find((s) => s.station_id === meta.stationId);
      const stationPos = station
        ? stationPosition(star, station, safeRadiiStations)
        : { xPct: popup.xPct, yPct: popup.yPct };
      // canonical-%-space: REFERENCE_BAND, same reasoning as
      // withinLandRange above -- this gates DOCK (-> onRequestDock -> POST
      // /trading/dock), which the server re-checks at the identical fixed
      // reference band.
      const withinDockRange = Boolean(
        shipPos &&
        distancePx(shipPos, stationPos, REFERENCE_BAND) <= DOCK_RANGE_EM * REFERENCE_BAND.remPx
      );
      const approachingThisStation = localTraveling && glideTargetId === meta.stationId;
      return (
        <>
          <div className="ssv-popup-title">{popup.name.toUpperCase()}</div>
          <div className="ssv-popup-line">{meta.stationType.replace(/_/g, ' ').toUpperCase()}</div>
          {approachingThisStation ? (
            <button
              type="button"
              className="ssv-popup-action halt"
              onClick={() => onHaltApproach()}
              aria-label={`Halt approach to ${popup.name}`}
            >
              🛑 HALT
            </button>
          ) : withinDockRange && onRequestDock ? (
            <button
              type="button"
              className="ssv-popup-action"
              onClick={() => { onClosePopup(); onRequestDock(meta.stationId); }}
            >
              ⚓ DOCK
            </button>
          ) : station ? (
            <button
              type="button"
              className="ssv-popup-action"
              onClick={() => onApproachStation(station, stationPos)}
            >
              ➤ APPROACH
            </button>
          ) : null}
          {!withinDockRange && !approachingThisStation && (
            <div className="ssv-popup-status">OUTSIDE DOCKING RANGE</div>
          )}
        </>
      );
    }
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
    case 'beacon':
      return (
        <BeaconPopupActions
          beaconId={meta.beaconId}
          deployerNickname={meta.deployerNickname}
          preview={meta.preview}
          deployedAt={meta.deployedAt}
          onClosePopup={onClosePopup}
          onBeaconRemoved={onBeaconRemoved}
        />
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
}

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

function serverDetail(err: unknown): string | undefined {
  if (err && typeof err === 'object') {
    const rawDetail = (err as { response?: { data?: { detail?: unknown } } }).response?.data
      ?.detail;
    if (typeof rawDetail === 'string' && rawDetail.trim()) return rawDetail.trim();
  }
  const message = err instanceof Error ? err.message : undefined;
  if (
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim())
  ) {
    return message.trim();
  }
  return undefined;
}

/** apiRequest throws Error with `.status`; beacons.py surfaces 400/404 detail strings. */
export function formatBeaconPopupError(err: unknown): string {
  const status = httpStatus(err);
  const detail = serverDetail(err);

  if (detail?.includes('ERR_PERSONAL_REP_TOO_LOW')) {
    return detail;
  }

  if (status === 404) {
    if (detail) return detail;
    return 'Beacon not found';
  }

  if (detail) return detail;
  return 'Action failed';
}

/** Sector-view Read/Salvage (message-beacons.md:42-47, :136-137). Visitor-capable. */
function BeaconPopupActions({
  beaconId,
  deployerNickname,
  preview,
  deployedAt,
  onClosePopup,
  onBeaconRemoved,
}: {
  beaconId: string;
  deployerNickname: string;
  preview: string;
  deployedAt: string | null;
  onClosePopup: () => void;
  onBeaconRemoved?: (beaconId: string) => void;
}): React.ReactNode {
  const [busy, setBusy] = useState<'read' | 'salvage' | null>(null);
  const [error, setError] = useState('');
  const [fullMessage, setFullMessage] = useState<string | null>(null);
  const [author, setAuthor] = useState<string | null>(null);

  const run = async (action: 'read' | 'salvage') => {
    if (busy) return;
    setBusy(action);
    setError('');
    try {
      if (action === 'read') {
        const result = await beaconAPI.read(beaconId);
        const message = typeof result?.message === 'string' ? result.message : '';
        const nick =
          typeof result?.deployer_nickname === 'string' && result.deployer_nickname
            ? result.deployer_nickname
            : deployerNickname;
        setFullMessage(message);
        setAuthor(nick);
        if (result?.read_once) {
          onBeaconRemoved?.(beaconId);
        }
      } else {
        await beaconAPI.salvage(beaconId);
        onBeaconRemoved?.(beaconId);
        onClosePopup();
      }
    } catch (err) {
      setError(formatBeaconPopupError(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <div className="ssv-popup-title">MESSAGE BEACON</div>
      <div className="ssv-popup-line">FROM — {deployerNickname.toUpperCase()}</div>
      {fullMessage != null ? (
        <>
          <div className="ssv-popup-line" data-testid="beacon-popup-full-message">{fullMessage}</div>
          {author && (
            <div className="ssv-popup-line" data-testid="beacon-popup-author">AUTHOR — {author}</div>
          )}
        </>
      ) : (
        <div className="ssv-popup-line">"{preview}"</div>
      )}
      <div className="ssv-popup-status">
        MESSAGE IN A BOTTLE — {deployedAt ? new Date(deployedAt).toLocaleDateString() : 'UNDATED'}
      </div>
      {error && (
        <div className="ssv-popup-status" role="alert" data-testid="beacon-popup-error">{error}</div>
      )}
      <button
        type="button"
        className="ssv-popup-action"
        data-testid="beacon-popup-read"
        disabled={busy != null}
        onClick={() => { void run('read'); }}
      >
        {busy === 'read' ? 'READING…' : 'READ (0 TURNS)'}
      </button>
      <button
        type="button"
        className="ssv-popup-action"
        data-testid="beacon-popup-salvage"
        disabled={busy != null}
        onClick={() => { void run('salvage'); }}
      >
        {busy === 'salvage' ? 'SALVAGING…' : 'SALVAGE (1 TURN · 250CR)'}
      </button>
    </>
  );
}
