import React, { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import './confirm-dialog.css';

/**
 * HailComposeDialog — the TACTICAL TARGET menu's "Hail" item opens this
 * (WO-TACTICAL-POPUP), replacing the old inline `.target-hail-compose` row
 * strip. Deliberately a SEPARATE component from ConfirmDialog.tsx (not an
 * extension of it) -- ConfirmDialog's title/message/confirm/cancel shape
 * has several existing consumers across the tactical surfaces and adding
 * a text-input mode to it would widen that shared component's contract for
 * every caller. Reuses ConfirmDialog's exact CSS classes/idiom (portal to
 * document.body, role="dialog" + aria-modal, mount-focus + focus-return,
 * Tab-cycle trap, Escape-to-close, backdrop click with a mount-debounce)
 * so both dialogs read as the same in-fiction CRT chrome.
 */

interface HailComposeDialogProps {
  contactName: string;
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onCancel: () => void;
  busy: boolean;
  /** Set only while composing -- a prior TRANSMITTED result closes this
   *  dialog (sendHail clears hailKey on success), so only a failure ever
   *  reaches here for display. */
  error?: string | null;
}

const HailComposeDialog: React.FC<HailComposeDialogProps> = ({
  contactName,
  value,
  onChange,
  onSend,
  onCancel,
  busy,
  error,
}) => {
  const inputRef = useRef<HTMLInputElement>(null);
  const sendRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const mountedAtRef = useRef(performance.now());
  const dismiss = () => {
    if (!busy) onCancel();
  };

  useEffect(() => {
    const previouslyFocused =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    inputRef.current?.focus();
    return () => previouslyFocused?.focus();
  }, []);

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        dismiss();
      } else if (e.key === 'Tab') {
        e.preventDefault();
        const focusables = [inputRef.current, sendRef.current, cancelRef.current].filter(Boolean) as HTMLElement[];
        if (focusables.length === 0) return;
        const idx = focusables.indexOf(document.activeElement as HTMLElement);
        const nextIdx = e.shiftKey
          ? (idx - 1 + focusables.length) % focusables.length
          : (idx + 1) % focusables.length;
        focusables[Math.max(nextIdx, 0)].focus();
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy]);

  return createPortal(
    <div
      className="confirm-dialog-overlay"
      onMouseDown={(e) => {
        e.stopPropagation();
        if (performance.now() - mountedAtRef.current < 250) return;
        dismiss();
      }}
      onClick={(e) => e.stopPropagation()}
    >
      <div
        className="confirm-dialog-panel"
        role="dialog"
        aria-modal="true"
        aria-label={`Hail message to ${contactName}`}
        onMouseDown={(e) => e.stopPropagation()}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="confirm-dialog-header">
          <span className="confirm-dialog-title">HAIL — {contactName}</span>
        </div>
        <div className="confirm-dialog-message">
          <input
            ref={inputRef}
            type="text"
            className="target-hail-input"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder="TRANSMISSION…"
            maxLength={500}
            disabled={busy}
            aria-label={`Hail message to ${contactName}`}
            // Only one HailComposeDialog is ever mounted at a time (one
            // `composing` row), so a static id is safe -- no duplicate-id
            // risk. Only set when an error exists so a passing SR user
            // never gets pointed at an absent node.
            aria-describedby={error ? 'hail-compose-error' : undefined}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && value.trim() && !busy) {
                e.preventDefault();
                onSend();
              }
            }}
          />
          {error && (
            <p id="hail-compose-error" className="target-result-msg err" role="alert">
              {error}
            </p>
          )}
        </div>
        <div className="confirm-dialog-actions">
          <button
            ref={cancelRef}
            type="button"
            className="confirm-dialog-btn cancel"
            onClick={dismiss}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            ref={sendRef}
            type="button"
            className="confirm-dialog-btn confirm"
            onClick={onSend}
            disabled={busy || !value.trim()}
            aria-busy={busy}
          >
            {busy ? '…' : 'Send'}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
};

export default HailComposeDialog;
