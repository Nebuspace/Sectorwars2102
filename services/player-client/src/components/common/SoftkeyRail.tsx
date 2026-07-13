import React, { useRef } from 'react';

/**
 * SoftkeyRail — the ONE shared roving-tabindex softkey/tab rail behind
 * both mfd/MFDSoftkeyRail's bottom MFD keys and cockpit/DeckPageTabs'
 * deck-monitor/station/colony header tabs (WO-UI0-SHELL-TRANSPLANT,
 * register D7 / §05 accept: "one softkey component drives MFDs,
 * monitors, station tabs, colony tabs — grep-provable single source").
 *
 * This file is the single place `role="tablist"`/`role="tab"` markup and
 * the roving-tabindex keyboard state machine are defined. Both callers
 * are thin adapters that translate their own (deliberately different,
 * already-shipped, multi-consumer) prop shapes into `SoftkeyRailItem[]`
 * and a handful of variant knobs — every visible class, id, disabled
 * attribute, and accent value is still decided by the caller so each
 * variant's DOM/CSS stays byte-identical to before this consolidation.
 *
 * Two interaction models coexist by design, not accident:
 *   - MFD ("manual activation"): ArrowLeft/Right only MOVE focus among
 *     the softkeys, skipping any `disabled` one; selection happens via
 *     native Enter/Space activation of the focused button.
 *   - Deck ("automatic activation"): ArrowLeft/Right (and Home/End) both
 *     move focus AND immediately select — matches the WAI-ARIA APG
 *     "automatic activation" tablist pattern DeckPageTabs' 9 call sites
 *     already ship. `activateOnArrow` + `homeEnd` select which model a
 *     given rail uses; do not conflate them.
 *
 * Filtering (hidden-vs-disabled, <2-pages-renders-null, 5-slot cap) is
 * deliberately NOT done here — those are caller-specific policies
 * (DeckPageTabs pre-filters unavailable pages out entirely; MFD renders
 * them disabled-in-place and caps at 5) and stay in the two adapters.
 */

export interface SoftkeyRailItem {
  /** Stable identity, used as the React key and passed back via onSelect closures owned by the caller. */
  key: string;
  /** Rendered button content — plain text or a caller-composed node (e.g. an unread-dot or alert badge). */
  label: React.ReactNode;
  selected: boolean;
  /** MFD variant only: renders in-place as a native-disabled button, skipped by arrow navigation. Deck items never set this (unavailable pages are filtered out by the caller before they reach this component). */
  disabled?: boolean;
  onSelect: () => void;
  /** Per-item accent override. Falls back to the rail's `railAccent` when omitted. */
  accent?: string;
  /** Deck variant only: wires the tablist→tabpanel a11y association the caller's sibling tabpanel expects. */
  id?: string;
  ariaControls?: string;
  /** MFD variant only: full aria-label override (e.g. "STATUS — alert"); Deck relies on visible text content as its accessible name. */
  ariaLabel?: string;
}

export interface SoftkeyRailProps {
  items: SoftkeyRailItem[];
  /** role=tablist aria-label on the rail root. */
  ariaLabel: string;
  /** Exact class string for the rail root div — callers own their own CSS family (mfd.css's .mfd-softkey-rail vs cockpit.css's .deck-tab-rail). */
  railClassName: string;
  itemClassName: (item: SoftkeyRailItem, index: number) => string;
  /** CSS custom-property name written per-item (and at the rail root when railAccent is set), e.g. '--mfd-key-accent' or '--tab-accent'. */
  accentVar: string;
  /** When set, also applied as an inline custom property at the rail root (Deck's rail-level --tab-accent). Omit for MFD, which sets no rail-level accent. */
  railAccent?: string;
  /** true = ArrowLeft/Right immediately select (Deck's automatic-activation model). false = ArrowLeft/Right only move focus; selection needs native Enter/Space activation (MFD). */
  activateOnArrow: boolean;
  /** true = also handle Home/End (Deck). false = arrows only (MFD). */
  homeEnd: boolean;
}

const SoftkeyRail: React.FC<SoftkeyRailProps> = ({
  items,
  ariaLabel,
  railClassName,
  itemClassName,
  accentVar,
  railAccent,
  activateOnArrow,
  homeEnd,
}) => {
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const activeIndex = items.findIndex((item) => item.selected);
  const tabStopIndex = activeIndex !== -1 ? activeIndex : items.findIndex((item) => !item.disabled);

  // Skip-search identical to MFDSoftkeyRail's original loop: walk up to
  // `count` steps from `fromIndex` in `direction`, wrapping, and return
  // the first non-disabled index found (or -1 if none). A no-op search
  // (always finds the very next index on step 1) whenever no item in the
  // rail is ever disabled — i.e. every Deck call site, which pre-filters
  // unavailable pages out before they ever reach this component.
  const findNextEnabled = (fromIndex: number, direction: 1 | -1): number => {
    const count = items.length;
    let next = fromIndex;
    for (let step = 0; step < count; step++) {
      next = (next + direction + count) % count;
      if (!items[next].disabled) return next;
    }
    return -1;
  };

  const moveTo = (index: number): void => {
    if (activateOnArrow) items[index].onSelect();
    itemRefs.current[index]?.focus();
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, index: number): void => {
    switch (event.key) {
      case 'ArrowRight':
      case 'ArrowLeft': {
        const next = findNextEnabled(index, event.key === 'ArrowRight' ? 1 : -1);
        if (next === -1) return;
        event.preventDefault();
        moveTo(next);
        return;
      }
      case 'Home':
        if (!homeEnd) return;
        event.preventDefault();
        moveTo(0);
        return;
      case 'End':
        if (!homeEnd) return;
        event.preventDefault();
        moveTo(items.length - 1);
        return;
      default:
        return;
    }
  };

  return (
    <div
      className={railClassName}
      role="tablist"
      aria-label={ariaLabel}
      style={railAccent !== undefined ? ({ [accentVar]: railAccent } as React.CSSProperties) : undefined}
    >
      {items.map((item, index) => (
        <button
          key={item.key}
          ref={(el) => {
            itemRefs.current[index] = el;
          }}
          type="button"
          role="tab"
          id={item.id}
          aria-controls={item.ariaControls}
          className={itemClassName(item, index)}
          style={{ [accentVar]: item.accent ?? railAccent } as React.CSSProperties}
          aria-selected={item.selected}
          aria-label={item.ariaLabel}
          aria-disabled={item.disabled}
          disabled={item.disabled}
          tabIndex={index === tabStopIndex ? 0 : -1}
          onClick={item.onSelect}
          onKeyDown={(event) => handleKeyDown(event, index)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
};

export default SoftkeyRail;
