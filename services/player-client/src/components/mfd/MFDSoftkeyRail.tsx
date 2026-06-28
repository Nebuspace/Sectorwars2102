/**
 * MFDSoftkeyRail — bottom tablist of physical-feel softkeys.
 *
 * Tap = select; no long-press grammar anywhere. Real buttons with
 * role="tab", roving tabindex, ArrowLeft/Right focus movement (Enter/
 * Space select via native button activation). Hidden pages are filtered
 * upstream by MFDScreen; max 5 keys, no paging.
 */

import React, { useRef } from 'react';
import type { MFDPageDef, MFDPageId, MFDSnapshot } from './mfdTypes';
import { isPageAvailable } from './mfdRegistry';

interface MFDSoftkeyRailProps {
  screenLabel: string;
  /** Visible (non-hidden) page defs in config order. */
  pages: MFDPageDef[];
  snapshot: MFDSnapshot;
  activePageId: MFDPageId;
  hasAlert: (pageId: MFDPageId) => boolean;
  onSelect: (pageId: MFDPageId) => void;
}

const MAX_SOFTKEYS = 5;

const MFDSoftkeyRail: React.FC<MFDSoftkeyRailProps> = ({
  screenLabel,
  pages,
  snapshot,
  activePageId,
  hasAlert,
  onSelect,
}) => {
  const keys = pages.slice(0, MAX_SOFTKEYS);
  const keyRefs = useRef<Array<HTMLButtonElement | null>>([]);

  // Roving tabindex anchor. If the active page's key is absent (it just
  // went hidden and the screen is mid-fallback), the first enabled key
  // keeps the rail reachable by keyboard.
  const activeKeyIndex = keys.findIndex((def) => def.id === activePageId);
  const tabStopIndex =
    activeKeyIndex !== -1
      ? activeKeyIndex
      : keys.findIndex((def) => isPageAvailable(def, snapshot));

  const handleKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, index: number): void => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    const direction = event.key === 'ArrowRight' ? 1 : -1;
    const count = keys.length;
    let next = index;
    for (let step = 0; step < count; step++) {
      next = (next + direction + count) % count;
      if (isPageAvailable(keys[next], snapshot)) {
        keyRefs.current[next]?.focus();
        return;
      }
    }
  };

  return (
    <div className="mfd-softkey-rail" role="tablist" aria-label={`${screenLabel} pages`}>
      {keys.map((def, index) => {
        const isActive = def.id === activePageId;
        const available = isPageAvailable(def, snapshot);
        const alerted = hasAlert(def.id) && !isActive;
        return (
          <button
            key={def.id}
            ref={(el) => {
              keyRefs.current[index] = el;
            }}
            type="button"
            role="tab"
            className="mfd-key"
            style={{ '--mfd-key-accent': def.accent } as React.CSSProperties}
            aria-selected={isActive}
            aria-label={alerted ? `${def.title} — alert` : def.title}
            aria-disabled={!available}
            disabled={!available}
            tabIndex={index === tabStopIndex ? 0 : -1}
            onClick={() => onSelect(def.id)}
            onKeyDown={(event) => handleKeyDown(event, index)}
          >
            {def.softLabel}
            {alerted ? <span className="mfd-key-badge" aria-hidden="true" /> : null}
          </button>
        );
      })}
    </div>
  );
};

export default MFDSoftkeyRail;
