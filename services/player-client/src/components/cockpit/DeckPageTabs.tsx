import React from 'react';
import SoftkeyRail, { type SoftkeyRailItem } from '../common/SoftkeyRail';

/**
 * DeckPageTabs — the ONE shared switchable-page rail for a deck monitor's
 * screen-hud-header, station terminal tabs, and colony-management tabs
 * (WO-UI2-DECK-MONITORS). Replaces three hand-copied, near-identical
 * switches that had drifted apart: NAV's WARP-GRAPH/QUANTUM-DRIVE
 * (nav-mode-switch), COMMS' CONTACTS/HAILS (comms-mode-switch), and SOLAR
 * SYSTEM's new BODIES/HAZARDS. Generic over a caller-defined page id — NOT
 * type-coupled to any one monitor's page union.
 *
 * This component's own public API — `DeckPage`/`DeckPageTabsProps`, the
 * `.deck-tab-rail`/`.deck-tab-btn` classes, the id/aria-controls a11y
 * wiring, the <2-pages-renders-null rule, per-page `accent` — is
 * unchanged and byte-identical to before WO-UI0-SHELL-TRANSPLANT; every
 * existing call site (GameDashboard NAV/SOLAR SYSTEM, TacticalMonitor,
 * CommsMailbox, CockpitColonyManagement, PortOfficeVenue,
 * ContractBoardVenue ×2, ConstructionVenue) needs zero changes. Only the
 * internals changed: rendering now delegates to the shared
 * common/SoftkeyRail.tsx primitive (register D7 / §05 accept: "one
 * softkey component drives MFDs, monitors, station tabs, colony tabs —
 * grep-provable single source"), which also backs mfd/MFDScreen.tsx's
 * bottom MFD keys. Availability/interaction model still differs from the
 * MFD side by design (see SoftkeyRail.tsx's doc-comment for why): an
 * unavailable page (e.g. QUANTUM DRIVE on a non-Warp-Jumper hull) is
 * filtered out entirely below, never rendered disabled-in-place, and
 * ArrowLeft/Right/Home/End here select immediately (automatic
 * activation) rather than only moving focus.
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

  // <2 available pages: no rail at all (not a single dead tab).
  if (tabs.length < 2) return null;

  const items: SoftkeyRailItem[] = tabs.map((page) => ({
    key: page.id,
    label: page.label,
    selected: page.id === activeId,
    onSelect: () => onSelect(page.id),
    accent: page.accent,
    id: `${idBase}-tab-${page.id}`,
    ariaControls: `${idBase}-panel-${page.id}`,
  }));

  return (
    <SoftkeyRail
      items={items}
      ariaLabel={ariaLabel}
      railClassName={`deck-tab-rail${className ? ' ' + className : ''}`}
      itemClassName={(item) => `deck-tab-btn${item.selected ? ' active' : ''}`}
      accentVar="--tab-accent"
      railAccent={accent}
      activateOnArrow
      homeEnd
    />
  );
};

export default DeckPageTabs;
