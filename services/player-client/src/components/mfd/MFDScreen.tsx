/**
 * MFDScreen — one physical multi-function display in the left console.
 *
 * Owns the bezel chrome, the page viewport (behind MFDPageBoundary +
 * Suspense), and the softkey rail (OUTSIDE the boundary — navigation
 * survives any page crash). The MFDSnapshot is assembled exactly once
 * here, memoized, and fed only to predicates; pages read hooks
 * themselves.
 */

import React, { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { useGame } from '../../contexts/GameContext';
import { useWebSocket } from '../../contexts/WebSocketContext';
import type { MFDPageDef, MFDPageId, MFDScreenConfig, MFDSnapshot } from './mfdTypes';
import { getPageDef, isPageAvailable, isPageHidden } from './mfdRegistry';
import { useMFD, useMFDScreenInternal } from './MFDContext';
import { deriveInitialPage } from './situations';
import { readPersistedPage } from './persistence';
import MFDPageBoundary from './MFDPageBoundary';
import SoftkeyRail, { type SoftkeyRailItem } from '../common/SoftkeyRail';
import { MFDPageSkeleton } from './atoms';
import './mfd.css';

// Bottom softkey rail: hard 5-slot cap, no paging (WO-UI1-CHROME-COMPLETE).
// The rail itself now lives in common/SoftkeyRail.tsx (WO-UI0-SHELL-
// TRANSPLANT, register D7) — this screen still owns which of its visible
// pages become keys (the 5-slot cap) and how each key looks (disabled-in-
// place for an unavailable-but-visible page, the alert badge), exactly as
// mfd/MFDSoftkeyRail.tsx did before the consolidation.
export const MAX_SOFTKEYS = 5;

export const buildSoftkeyItems = (
  visiblePages: MFDPageDef[],
  snapshot: MFDSnapshot,
  activePageId: MFDPageId,
  hasAlert: (pageId: MFDPageId) => boolean,
  onSelect: (pageId: MFDPageId) => void,
): SoftkeyRailItem[] =>
  visiblePages.slice(0, MAX_SOFTKEYS).map((def) => {
    const isActive = def.id === activePageId;
    const available = isPageAvailable(def, snapshot);
    const alerted = hasAlert(def.id) && !isActive;
    return {
      key: def.id,
      label: (
        <>
          {def.softLabel}
          {alerted ? <span className="mfd-key-badge" aria-hidden="true" /> : null}
        </>
      ),
      selected: isActive,
      disabled: !available,
      onSelect: () => onSelect(def.id),
      accent: def.accent,
      ariaLabel: alerted ? `${def.title} — alert` : def.title,
    };
  });

// Design A graft: ONE memoized snapshot per screen render, so predicate
// evaluation never tears between softkeys.
const useMFDSnapshot = (): MFDSnapshot => {
  const { currentShip, playerState, currentSector } = useGame();
  const { isConnected } = useWebSocket();
  return useMemo<MFDSnapshot>(
    () => ({ currentShip, playerState, currentSector, isConnected }),
    [currentShip, playerState, currentSector, isConnected],
  );
};

const MFDScreen: React.FC<{ config: MFDScreenConfig }> = ({ config }) => {
  const snapshot = useMFDSnapshot();
  const { activeFor, selectPage, hasAlert } = useMFD();
  const { registerScreen } = useMFDScreenInternal();

  // On a cold load GameContext is still fetching, so the snapshot is
  // all-null at mount; predicate validation against it would wrongly
  // reject (and then permanently rewrite away) a legitimate persisted
  // page like quantum-drive. Mount accepts on MEMBERSHIP ONLY; predicate
  // enforcement waits for hydration below.
  const hydrated = snapshot.playerState !== null || snapshot.currentShip !== null;
  const persistedAtMountRef = useRef(false);
  const [initialPageId] = useState<MFDPageId>(() => {
    const persisted = readPersistedPage(config.screenId);
    if (persisted !== null && (config.pageIds as string[]).includes(persisted)) {
      persistedAtMountRef.current = true;
      return persisted as MFDPageId;
    }
    return deriveInitialPage(config.screenId, snapshot);
  });
  const userTouchedRef = useRef(false);

  useEffect(() => {
    registerScreen(config.screenId, config.pageIds, config.defaultPageId, initialPageId);
  }, [registerScreen, config, initialPageId]);

  const activePageId = activeFor(config.screenId) ?? initialPageId;
  const activeDef = getPageDef(activePageId);

  // Late situation default (Design B graft): with no persisted choice the
  // docked-aware default can only be computed once player data arrives —
  // apply it exactly once, and never over a page the user already chose.
  const situatedRef = useRef(false);
  useEffect(() => {
    if (!hydrated || situatedRef.current) return;
    situatedRef.current = true;
    if (persistedAtMountRef.current || userTouchedRef.current) return;
    const situated = deriveInitialPage(config.screenId, snapshot);
    if (situated !== activePageId) {
      selectPage(config.screenId, situated);
    }
  }, [hydrated, snapshot, activePageId, selectPage, config.screenId]);

  const visiblePages = useMemo(
    () => config.pageIds.map(getPageDef).filter((def) => !isPageHidden(def, snapshot)),
    [config.pageIds, snapshot],
  );

  // Eviction guard (not situation live-switching): if the active page's
  // softkey ceases to exist — e.g. quantum-drive after switching off a
  // Warp Jumper — retreat to the first visible page so the rail and the
  // viewport never disagree. Gated on hydration: a null snapshot hides
  // quantum-drive spuriously and must not evict a persisted selection.
  useEffect(() => {
    if (!hydrated) return;
    if (!isPageHidden(activeDef, snapshot)) return;
    const fallback = visiblePages[0];
    if (fallback !== undefined && fallback.id !== activePageId) {
      selectPage(config.screenId, fallback.id);
    }
  }, [hydrated, activeDef, activePageId, snapshot, visiblePages, selectPage, config.screenId]);

  const ActivePage = activeDef.Component;

  return (
    <section
      className="mfd-screen"
      style={{ '--mfd-accent': activeDef.accent } as React.CSSProperties}
      aria-label={`${config.systemLabel} multi-function display`}
    >
      <span className="mfd-rivet mfd-rivet-tl" aria-hidden="true" />
      <span className="mfd-rivet mfd-rivet-tr" aria-hidden="true" />
      <span className="mfd-rivet mfd-rivet-bl" aria-hidden="true" />
      <span className="mfd-rivet mfd-rivet-br" aria-hidden="true" />
      <span className="mfd-system-label">{config.systemLabel}</span>
      {/* Persistent live region: announces page swaps to assistive tech.
          Must live OUTSIDE the remounting page tree — fresh live regions
          are not reliably announced; content changes in a stable one are. */}
      <span className="mfd-visually-hidden" aria-live="polite">
        {`${config.systemLabel}: ${activeDef.title}`}
      </span>
      <div className="mfd-viewport" role="tabpanel" aria-label={activeDef.title}>
        <MFDPageBoundary resetKey={activePageId}>
          <Suspense fallback={<MFDPageSkeleton />}>
            <ActivePage />
          </Suspense>
        </MFDPageBoundary>
      </div>
      <SoftkeyRail
        items={buildSoftkeyItems(visiblePages, snapshot, activePageId, hasAlert, (pageId) => {
          userTouchedRef.current = true;
          selectPage(config.screenId, pageId);
        })}
        ariaLabel={`${config.systemLabel} pages`}
        railClassName="mfd-softkey-rail"
        itemClassName={() => 'mfd-key'}
        accentVar="--mfd-key-accent"
        activateOnArrow={false}
        homeEnd={false}
      />
    </section>
  );
};

export default MFDScreen;
