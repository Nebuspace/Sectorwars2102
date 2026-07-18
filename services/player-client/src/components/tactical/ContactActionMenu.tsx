import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

/**
 * ContactActionMenu — small anchored popup menu opened by clicking a
 * TACTICAL TARGET contact's name (WO-TACTICAL-POPUP). Replaces the row's
 * former inline ENGAGE/HAIL buttons with a single a11y menu, following the
 * SAME portal-to-body idiom ConfirmDialog.tsx already uses in this
 * directory (ancestor transforms/overflow/z-index in the deck-monitor
 * scroll body can never clip it).
 *
 * `anchorEl` is read at mount time only (a contact row's trigger span is
 * always rendered, so it's already attached by the time a click opens this
 * menu) -- position is computed once via getBoundingClientRect + clamped
 * to the viewport, mirroring WindshieldTableau's ctxMenu clamped-anchor
 * idiom for its own right-click menu.
 */

export interface ContactActionMenuItem {
  key: string;
  label: string;
  variant?: 'engage' | 'hail';
  onSelect: () => void;
}

interface ContactActionMenuProps {
  anchorEl: HTMLElement | null;
  items: ContactActionMenuItem[];
  label: string;
  onClose: () => void;
}

const MENU_MARGIN = 6;

const ContactActionMenu: React.FC<ContactActionMenuProps> = ({ anchorEl, items, label, onClose }) => {
  const menuRef = useRef<HTMLDivElement>(null);
  const [style, setStyle] = useState<React.CSSProperties>({ visibility: 'hidden' });
  // Mount timestamp: ignore outside-click dismissals fired within the same
  // gesture that opened the menu (mousedown-before-click ordering on the
  // trigger itself never reaches here since the trigger is inside
  // `anchorEl`, but a fast double-click elsewhere shouldn't punch through).
  const mountedAtRef = useRef(performance.now());

  useLayoutEffect(() => {
    const menu = menuRef.current;
    if (!anchorEl || !menu) return;
    const anchorRect = anchorEl.getBoundingClientRect();
    const menuRect = menu.getBoundingClientRect();
    const left = Math.min(
      Math.max(MENU_MARGIN, anchorRect.left),
      window.innerWidth - menuRect.width - MENU_MARGIN
    );
    // Flip up when there's no room below the anchor -- a bottom-of-list
    // contact would otherwise just get clamped upward by the Math.min
    // below, landing the menu overlapping its own trigger row instead of
    // opening in the natural direction (WO-TACTICAL-POPUP browser-prove
    // note: visible but fragile). The final clamp stays as a backstop for
    // the (rarer) case where even the above-anchor position overflows the
    // top -- same clamped-anchor idiom as the below-anchor path.
    const fitsBelow = anchorRect.bottom + 4 + menuRect.height + MENU_MARGIN <= window.innerHeight;
    const preferredTop = fitsBelow ? anchorRect.bottom + 4 : anchorRect.top - menuRect.height - 4;
    const top = Math.min(
      Math.max(MENU_MARGIN, preferredTop),
      window.innerHeight - menuRect.height - MENU_MARGIN
    );
    setStyle({ left, top });
  }, [anchorEl]);

  // Initial focus lands on the first item -- WAI-ARIA menu pattern; focus
  // returns to the trigger on unmount (Escape, outside-click, or an item
  // being chosen all funnel through the same unmount).
  useEffect(() => {
    const first = menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]');
    first?.focus();
    return () => anchorEl?.focus();
  }, [anchorEl]);

  useEffect(() => {
    const focusables = () =>
      Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);

    const moveFocus = (delta: number) => {
      const list = focusables();
      if (list.length === 0) return;
      const idx = list.indexOf(document.activeElement as HTMLElement);
      const next = list[(idx + delta + list.length) % list.length];
      next.focus();
    };

    const onPointerDown = (e: MouseEvent) => {
      if (performance.now() - mountedAtRef.current < 150) return;
      const target = e.target as Node;
      if (menuRef.current?.contains(target) || anchorEl?.contains(target)) return;
      onClose();
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      } else if (e.key === 'ArrowDown' || (e.key === 'Tab' && !e.shiftKey)) {
        e.preventDefault();
        moveFocus(1);
      } else if (e.key === 'ArrowUp' || (e.key === 'Tab' && e.shiftKey)) {
        e.preventDefault();
        moveFocus(-1);
      }
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [onClose, anchorEl]);

  return createPortal(
    <div
      ref={menuRef}
      className="contact-action-menu"
      style={style}
      role="menu"
      aria-label={label}
    >
      {items.map((item) => (
        <button
          key={item.key}
          type="button"
          role="menuitem"
          className={`contact-action-menu-item${item.variant ? ` contact-action-menu-item-${item.variant}` : ''}`}
          onClick={item.onSelect}
        >
          {item.label}
        </button>
      ))}
    </div>,
    document.body
  );
};

export default ContactActionMenu;
