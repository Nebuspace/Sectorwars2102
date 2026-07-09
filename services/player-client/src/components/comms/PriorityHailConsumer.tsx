import React, { useEffect, useRef, useState } from 'react';
import ReactDOM from 'react-dom';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { useGame } from '../../contexts/GameContext';
import './priority-hail-consumer.css';

// WO-PUX-UPLINK-HUD: a blip shorter than this never surfaces a toast — most
// backoff retries recover well inside it and a toast per retry would be
// noise, not signal. NO-CANON threshold, flagged for ratification.
const UPLINK_TOAST_DEBOUNCE_MS = 2000;

/**
 * PriorityHailConsumer — the cockpit's priority-driven notification surfaces.
 *
 * Renders the two delivery surfaces the messaging canon
 * (sw2102-docs/FEATURES/gameplay/messaging.md → "Priority levels") layers on
 * top of the always-present inbox:
 *
 *   • TOAST  — the in-game notification toast for `normal`/`high`/`urgent`
 *              messages. Driven by the shared WebSocket `notifications` queue
 *              from WebSocketContext (which the backend now only fills for a
 *              message when its delivery list includes `toast` — `low` is
 *              inbox-only and never toasts). This is the FIRST live renderer of
 *              that queue in the cockpit, so it also surfaces other WS toasts
 *              (trade, combat, ARIA, medals) that previously dropped silently.
 *   • MODAL  — the action-interrupting modal for `urgent` messages (admin
 *              senders only — see notification_service.delivery_surfaces_for).
 *              Driven by `urgentMessageSignal` / `lastUrgentMessage`.
 *
 * `low`-priority messages produce NEITHER surface — only the unread badge /
 * inbox refresh (handled by CommsMailbox off `newMessageSignal`), honoring the
 * canon "inbox only" behavior.
 *
 * Also owns the uplink lost/restored toast pair (WO-PUX-UPLINK-HUD): a
 * debounced watch on WebSocketContext.linkStatus that surfaces the SAME toast
 * queue above, keeping the "one place chrome renders a toast" invariant
 * rather than adding a second toast surface elsewhere.
 *
 * Mounted once in GameLayout (inside WebSocketProvider, always present on /game
 * routes). The modal renders through a portal to document.body so it overlays
 * the entire cockpit regardless of the layout's stacking context.
 */

const PriorityHailConsumer: React.FC = () => {
  const { notifications, removeNotification, urgentMessageSignal, lastUrgentMessage, linkStatus, addNotification } =
    useWebSocket();
  // markMessageRead lets "ACKNOWLEDGE" on the urgent modal also clear the
  // unread state, so dismissing the interrupt doesn't leave a stale badge.
  const { markMessageRead } = useGame();

  // ── Uplink lost/restored toast pairing (WO-PUX-UPLINK-HUD) ──────────────
  // Exactly ONE "lost" + ONE "restored" toast per outage, no matter how many
  // backoff attempts happen in between. outageAnnouncedRef tracks whether the
  // CURRENT outage already crossed the debounce and got its "lost" toast (so
  // "restored" only fires when there is something to restore FROM); the
  // debounce timer is armed once per fresh up->down transition and cleared
  // the moment the link recovers before it fires (the blip case: no toast at
  // all). prevLinkStatusRef lets the effect see "up" was the PRIOR state on
  // mount, which the [linkStatus] dependency alone can't distinguish from a
  // real transition.
  const prevLinkStatusRef = useRef(linkStatus);
  const debounceTimerRef = useRef<number | null>(null);
  const outageAnnouncedRef = useRef(false);

  useEffect(() => {
    const prev = prevLinkStatusRef.current;
    prevLinkStatusRef.current = linkStatus;
    if (prev === linkStatus) return;

    if (linkStatus === 'up') {
      if (debounceTimerRef.current !== null) {
        window.clearTimeout(debounceTimerRef.current);
        debounceTimerRef.current = null;
      }
      if (outageAnnouncedRef.current) {
        outageAnnouncedRef.current = false;
        addNotification({
          title: 'Uplink restored',
          content: 'Live updates resumed.',
          level: 'success'
        });
      }
      return;
    }

    // linkStatus is now 'reconnecting' or 'down'. Only arm the debounce on a
    // FRESH loss (prev === 'up') — a further reconnecting<->down flap while
    // already mid-outage must not re-arm or double-toast.
    if (prev === 'up') {
      debounceTimerRef.current = window.setTimeout(() => {
        debounceTimerRef.current = null;
        outageAnnouncedRef.current = true;
        addNotification({
          title: 'Uplink lost — reconnecting',
          content: 'Live updates paused while the connection recovers.',
          level: 'warning'
        });
      }, UPLINK_TOAST_DEBOUNCE_MS);
    }
  }, [linkStatus, addNotification]);

  // Unmount safety: don't let a pending debounce fire into an unmounted tree.
  useEffect(() => () => {
    if (debounceTimerRef.current !== null) {
      window.clearTimeout(debounceTimerRef.current);
    }
  }, []);

  // ── Urgent modal ──────────────────────────────────────────────────────
  // The modal opens on each NEW urgent signal (not merely on a non-null
  // payload), so a second urgent hail re-raises the interrupt even if the
  // pilot had dismissed the first. Keyed on the monotonically-rising signal.
  const [modalSignalSeen, setModalSignalSeen] = useState(0);
  const [modalOpen, setModalOpen] = useState(false);
  const [modalMessageId, setModalMessageId] = useState<string | null>(null);

  useEffect(() => {
    if (urgentMessageSignal > modalSignalSeen && lastUrgentMessage) {
      setModalSignalSeen(urgentMessageSignal);
      setModalMessageId(lastUrgentMessage.message_id || null);
      setModalOpen(true);
    }
  }, [urgentMessageSignal, modalSignalSeen, lastUrgentMessage]);

  const dismissModal = () => {
    setModalOpen(false);
    if (modalMessageId) {
      // Acknowledging the interrupt counts as reading it; a failed read write
      // must not block dismissal.
      markMessageRead(modalMessageId).catch((err) =>
        console.warn('PriorityHailConsumer: failed to mark urgent message read:', err)
      );
    }
  };

  // ESC dismisses the modal (does not mark read — that is ACKNOWLEDGE only).
  useEffect(() => {
    if (!modalOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setModalOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [modalOpen]);

  const toastLevelClass = (level: string): string => {
    switch (level) {
      case 'success':
        return 'phc-toast--success';
      case 'warning':
        return 'phc-toast--warning';
      case 'error':
        return 'phc-toast--error';
      default:
        return 'phc-toast--info';
    }
  };

  return (
    <>
      {/* Toast stack — top-right of the cockpit, above the viewport chrome. */}
      <div className="phc-toast-stack" aria-live="polite" aria-atomic="false">
        {notifications.map((n, index) => (
          <div
            key={n.timestamp}
            className={`phc-toast ${toastLevelClass(n.level)}`}
            role="status"
          >
            <div className="phc-toast-body">
              <span className="phc-toast-title">{n.title}</span>
              {n.content && <span className="phc-toast-content">{n.content}</span>}
            </div>
            <button
              className="phc-toast-dismiss"
              onClick={() => removeNotification(index)}
              aria-label="Dismiss notification"
            >
              ×
            </button>
          </div>
        ))}
      </div>

      {/* Urgent interrupt modal — portal'd to body so it overlays everything. */}
      {modalOpen &&
        lastUrgentMessage &&
        ReactDOM.createPortal(
          <div
            className="phc-modal-backdrop"
            role="dialog"
            aria-modal="true"
            aria-labelledby="phc-modal-title"
            onClick={(e) => {
              // Backdrop click dismisses (without read) — the interrupt must be
              // escapable; ACKNOWLEDGE is the read action.
              if (e.target === e.currentTarget) setModalOpen(false);
            }}
          >
            <div className="phc-modal">
              <div className="phc-modal-header">
                <span className="phc-modal-flash" aria-hidden="true">⚠</span>
                <span id="phc-modal-title" className="phc-modal-title">
                  PRIORITY TRANSMISSION
                </span>
              </div>
              <div className="phc-modal-sender">
                FROM: {(lastUrgentMessage.sender_name || 'UNKNOWN').toUpperCase()}
              </div>
              <p className="phc-modal-preview">
                {lastUrgentMessage.preview || 'Urgent transmission received.'}
              </p>
              <button className="phc-modal-ack" onClick={dismissModal} autoFocus>
                ▸ ACKNOWLEDGE
              </button>
            </div>
          </div>,
          document.body
        )}
    </>
  );
};

export default PriorityHailConsumer;
