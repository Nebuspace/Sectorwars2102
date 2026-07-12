import React, { useRef } from 'react';

/**
 * DeckPageTabs — the ONE shared switchable-page rail for a deck monitor's
 * screen-hud-header (WO-UI2-DECK-MONITORS). Replaces three hand-copied,
 * near-identical switches that had drifted apart: NAV's WARP-GRAPH/QUANTUM-
 * DRIVE (nav-mode-switch), COMMS' CONTACTS/HAILS (comms-mode-switch), and
 * SOLAR SYSTEM's new BODIES/HAZARDS. Generic over a caller-defined page id —
 * NOT type-coupled to any one monitor's page union.
 *
 * Modeled on mfd/MFDSoftkeyRail.tsx's interaction (role=tablist/tab, roving
 * tabindex, ArrowLeft/Right wrap) and layouts/StatusBar.tsx's dossier
 * tablist (Home/End). Deliberately NOT a copy of MFDSoftkeyRail: that
 * component is type-coupled to the frozen MFDPageId union and expects a
 * LazyExoticComponent per page — wrong shape for a thin, page-content-
 * agnostic rail. Also deliberately not `.mfd-key` styling — mfd.css is a
 * different visual generation; this rail's CSS lives in cockpit.css's
 * `.deck-tab-rail`/`.deck-tab-btn`, parameterized by `--tab-accent` so each
 * monitor (NAV cyan, SOLAR SYSTEM purple, COMMS green) supplies its own
 * accent without a per-monitor CSS block.
 *
 * Availability differs from MFDSoftkeyRail's model too: an unavailable page
 * (e.g. QUANTUM DRIVE on a non-Warp-Jumper hull) is never rendered as a
 * disabled/dead tab — it's filtered out entirely before the rail's roving-
 * tabindex/arrow-nav math ever sees it. A monitor left with fewer than 2
 * available pages gets NO rail (returns null) rather than a lone,
 * unswitchable tab.
 */

export interface DeckPage {
  /** Stable page identity, passed back verbatim via onSelect. */
  id: string;
  /** Visible tab label. ReactNode (not just string) so a caller can embed
   *  a badge/dot (e.g. COMMS' unread pulse) alongside the text. */
  label: React.ReactNode;
  /** Defaults to true. false excludes the page from the rendered rail
   *  entirely (not shown disabled). */
  available?: boolean;
  /**
   * Optional CSS color value for THIS page's own --tab-accent (WO-UI2-
   * CANON-A-COLONY), overriding the rail's single `accent` prop for just
   * this button — e.g. CockpitColonyManagement's 7 tabs each carry a
   * distinct Law-5 accent (citadel amber, grid violet, terraform green…)
   * rather than one flat rail color. Omitted → the button falls back to
   * the rail's `accent`, byte-identical to the pre-existing single-accent
   * behavior (NAV/SOLAR SYSTEM/COMMS and the 3 venue migrations set no
   * page.accent and must render unchanged).
   */
  accent?: string;
}

interface DeckPageTabsProps {
  pages: DeckPage[];
  activeId: string;
  onSelect: (id: string) => void;
  /** role=tablist aria-label, e.g. "NAV display mode". */
  ariaLabel: string;
  /** CSS color value written to --tab-accent on the rail wrapper. */
  accent: string;
  /**
   * Stable per-rail id prefix so the 3 monitors' tab/panel ids never
   * collide (e.g. "nav", "system", "comms"). Each tab renders
   * `id="{idBase}-tab-{page.id}"` + `aria-controls="{idBase}-panel-{page.id}"`;
   * the parent must render its (single, swapped) active-page content
   * wrapped in a matching `role="tabpanel" id="{idBase}-panel-{activeId}"
   * aria-labelledby="{idBase}-tab-{activeId}"` — replicates
   * layouts/StatusBar.tsx's dossier tablist→tabpanel pattern (Pixel
   * INACCESSIBLE — the tablist/tab roles existed with no tabpanel
   * association for screen readers to navigate to).
   */
  idBase: string;
  /**
   * Optional caller-supplied class merged ADDITIVELY onto the rail root
   * (alongside 'deck-tab-rail', never replacing it) so a migrated venue
   * tablist keeps its per-venue skin (e.g. "cmc-tabbar") while adopting
   * this rail's shared tab behavior. Root only — never applied to the tab
   * buttons themselves.
   */
  className?: string;
}

const DeckPageTabs: React.FC<DeckPageTabsProps> = ({
  pages,
  activeId,
  onSelect,
  ariaLabel,
  accent,
  idBase,
  className,
}) => {
  const tabs = pages.filter((p) => p.available !== false);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  // <2 available pages: no rail at all (not a single dead tab).
  if (tabs.length < 2) return null;

  const activeIndexRaw = tabs.findIndex((p) => p.id === activeId);
  // Roving-tabindex anchor: if the active id isn't among the currently
  // rendered (available) tabs — e.g. mid-transition after a ship swap drops
  // a page — the first tab keeps the rail keyboard-reachable.
  const tabStopIndex = activeIndexRaw !== -1 ? activeIndexRaw : 0;

  const focusAndSelect = (index: number): void => {
    onSelect(tabs[index].id);
    tabRefs.current[index]?.focus();
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, index: number): void => {
    const count = tabs.length;
    switch (event.key) {
      case 'ArrowRight':
        event.preventDefault();
        focusAndSelect((index + 1) % count);
        return;
      case 'ArrowLeft':
        event.preventDefault();
        focusAndSelect((index - 1 + count) % count);
        return;
      case 'Home':
        event.preventDefault();
        focusAndSelect(0);
        return;
      case 'End':
        event.preventDefault();
        focusAndSelect(count - 1);
        return;
      default:
        return;
    }
  };

  return (
    <div
      className={`deck-tab-rail${className ? ' ' + className : ''}`}
      role="tablist"
      aria-label={ariaLabel}
      style={{ '--tab-accent': accent } as React.CSSProperties}
    >
      {tabs.map((page, index) => {
        const isActive = page.id === activeId;
        return (
          <button
            key={page.id}
            ref={(el) => {
              tabRefs.current[index] = el;
            }}
            type="button"
            role="tab"
            id={`${idBase}-tab-${page.id}`}
            aria-controls={`${idBase}-panel-${page.id}`}
            className={`deck-tab-btn${isActive ? ' active' : ''}`}
            aria-selected={isActive}
            tabIndex={index === tabStopIndex ? 0 : -1}
            style={{ '--tab-accent': page.accent ?? accent } as React.CSSProperties}
            onClick={() => onSelect(page.id)}
            onKeyDown={(event) => handleKeyDown(event, index)}
          >
            {page.label}
          </button>
        );
      })}
    </div>
  );
};

export default DeckPageTabs;
