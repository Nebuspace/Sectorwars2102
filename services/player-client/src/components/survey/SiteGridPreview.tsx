import React, { useMemo } from 'react';
import { siteUsableSlots, type SiteIntel } from './expeditionTypes';
import './site-grid-preview.css';

/**
 * Site-shape preview grid. Distinct from planetary/GridManager.tsx — that
 * component drives the AUTHORITATIVE post-settle citadel construction grid
 * (GET/POST /planets/{id}/grid, tied to structures.py placement). Before
 * settle there is no such grid: only usable_slots + shape_class exist on the
 * ephemeral SiteIntel payload, with no per-cell layout at all. Rather than
 * force an unrelated "fogged" prop onto GridManager (which expects real
 * plots/buildings/catalog), this renders a schematic cell approximation of
 * `usable_slots` — fogged (silhouette-only, no slot count shown) for a
 * not-yet-revealed/PENDING expedition, revealed once a result exists.
 */

interface SiteGridPreviewProps {
  result: SiteIntel | null | undefined;
  /** True while no result exists yet (PENDING, or no expedition at all). */
  fogged: boolean;
}

const gridDimsForSlots = (slots: number): [number, number] => {
  const cols = Math.max(1, Math.ceil(Math.sqrt(slots)));
  const rows = Math.max(1, Math.ceil(slots / cols));
  return [cols, rows];
};

const SiteGridPreview: React.FC<SiteGridPreviewProps> = ({ result, fogged }) => {
  const slots = siteUsableSlots(result) ?? 0;
  const [cols, rows] = useMemo(
    () => gridDimsForSlots(fogged || slots <= 0 ? 9 : slots),
    [fogged, slots],
  );
  const cellCount = cols * rows;

  return (
    <div className={`site-grid-preview${fogged ? ' fogged' : ''}`}>
      <div className="site-grid-preview-header">
        <span>{fogged ? 'Site — Unrevealed' : 'Site Preview'}</span>
        {!fogged && slots > 0 && <span className="site-grid-preview-slots">{slots} slots</span>}
      </div>
      <div
        className="site-grid-preview-board"
        style={{ gridTemplateColumns: `repeat(${cols}, 1fr)` }}
        role="img"
        aria-label={fogged ? 'Unrevealed site silhouette' : `Site shape, ${slots} usable slots`}
      >
        {Array.from({ length: cellCount }, (_, i) => (
          <div
            key={i}
            className={`site-grid-preview-cell${fogged ? ' fog' : ' revealed'}${
              !fogged && i >= slots ? ' unusable' : ''
            }`}
          />
        ))}
      </div>
      {fogged && (
        <div className="site-grid-preview-hint">Launch (or await) an expedition to reveal this site.</div>
      )}
    </div>
  );
};

export default SiteGridPreview;
