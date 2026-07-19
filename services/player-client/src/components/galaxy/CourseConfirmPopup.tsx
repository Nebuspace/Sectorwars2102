/**
 * CourseConfirmPopup — NAV 3D multi-hop route confirmation (ARIA Multi-Sector Warp).
 *
 * Shown after the player clicks a charted but non-adjacent sector. Displays
 * hop ledger + risk profile derived only from plot hop fields (visited /
 * safety_rating). Never fabricates safety for unvisited hops.
 */

import type { CourseHop, CourseReachable } from '../../contexts/AutopilotContext';

export type RiskBand = 'SAFE' | 'CAUTION' | 'HOSTILE' | 'UNKNOWN';

export function hopRiskBand(hop: CourseHop): RiskBand {
  if (!hop.visited || hop.safety_rating == null) return 'UNKNOWN';
  if (hop.safety_rating >= 0.7) return 'SAFE';
  if (hop.safety_rating >= 0.4) return 'CAUTION';
  return 'HOSTILE';
}

export function summarizeRouteRisk(hops: CourseHop[]): {
  band: RiskBand;
  unknownCount: number;
  unvisitedCount: number;
  label: string;
} {
  const bands = hops.map(hopRiskBand);
  const unknownCount = bands.filter((b) => b === 'UNKNOWN').length;
  const unvisitedCount = hops.filter((h) => !h.visited).length;
  if (bands.includes('HOSTILE')) {
    return {
      band: 'HOSTILE',
      unknownCount,
      unvisitedCount,
      label: 'HOSTILE LEGS ON ROUTE',
    };
  }
  if (unknownCount > 0) {
    return {
      band: 'UNKNOWN',
      unknownCount,
      unvisitedCount,
      label: 'UNCHARTED CONDITIONS',
    };
  }
  if (bands.includes('CAUTION')) {
    return {
      band: 'CAUTION',
      unknownCount,
      unvisitedCount,
      label: 'CAUTION — ELEVATED RISK',
    };
  }
  return {
    band: 'SAFE',
    unknownCount,
    unvisitedCount,
    label: 'CLEAR — CHARTED PATH',
  };
}

/** Standard ARIA command echoed into the teleprinter on commit. */
export function ariaEngageCommand(targetSectorId: number): string {
  return `ARIA, engage plotted course to Sector ${targetSectorId}.`;
}

interface CourseConfirmPopupProps {
  course: CourseReachable;
  plotting?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function CourseConfirmPopup({
  course,
  plotting = false,
  onConfirm,
  onCancel,
}: CourseConfirmPopupProps) {
  const risk = summarizeRouteRisk(course.hops);
  const destHop = course.hops[course.hops.length - 1];
  const destName = destHop?.name ?? `Sector ${course.target_sector_id}`;

  return (
    <div
      className="course-confirm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="course-confirm-title"
    >
      <div className="course-confirm__head">
        <span id="course-confirm-title" className="course-confirm__title">
          LAY IN COURSE
        </span>
        <button
          type="button"
          className="course-confirm__close"
          onClick={onCancel}
          aria-label="Cancel course"
        >
          ✕
        </button>
      </div>

      <div className="course-confirm__dest">
        <span className="course-confirm__dest-label">DESTINATION</span>
        <span className="course-confirm__dest-value">
          {destName}
          <span className="course-confirm__dest-id">#{course.target_sector_id}</span>
        </span>
      </div>

      <div className="course-confirm__meta">
        <span>{course.hops.length} HOP{course.hops.length === 1 ? '' : 'S'}</span>
        <span className="course-confirm__meta-sep">·</span>
        <span>{course.total_turns} TURNS</span>
      </div>

      <div
        className={`course-confirm__risk course-confirm__risk--${risk.band.toLowerCase()}`}
        role="status"
      >
        {risk.label}
        {risk.unvisitedCount > 0 && (
          <span className="course-confirm__risk-detail">
            {' '}· {risk.unvisitedCount} unvisited leg{risk.unvisitedCount === 1 ? '' : 's'}
          </span>
        )}
      </div>

      <ol className="course-confirm__hops">
        {course.hops.map((hop, i) => {
          const band = hopRiskBand(hop);
          return (
            <li key={`${hop.sector_id}-${i}`} className="course-confirm__hop">
              <span className="course-confirm__hop-n">{i + 1}</span>
              <span className="course-confirm__hop-name">
                {hop.name}
                {hop.via_tunnel ? ' ⌘' : ''}
              </span>
              <span className={`course-confirm__hop-band course-confirm__hop-band--${band.toLowerCase()}`}>
                {band === 'UNKNOWN' ? 'UNCHARTED' : band}
              </span>
              <span className="course-confirm__hop-turns">{hop.turn_cost}t</span>
            </li>
          );
        })}
      </ol>

      <div className="course-confirm__actions">
        <button
          type="button"
          className="course-confirm__cancel"
          onClick={onCancel}
          disabled={plotting}
        >
          Cancel
        </button>
        <button
          type="button"
          className="course-confirm__commit"
          onClick={onConfirm}
          disabled={plotting || course.hops.length === 0}
        >
          {plotting
            ? 'Plotting…'
            : `COMMIT ${course.total_turns} TURNS · GIVE TO ARIA`}
        </button>
      </div>
    </div>
  );
}
