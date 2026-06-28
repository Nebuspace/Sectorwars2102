import React, { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import './confirm-dialog.css';

/** Deferred-confirmation state shape shared by cards that open a ConfirmDialog. */
export interface PendingConfirm {
  title: string;
  message: string;
  confirmLabel: string;
  onConfirm: () => void;
}

interface ConfirmDialogProps {
  /** Short uppercase header, e.g. "DOCKING REQUEST" */
  title: string;
  /** Body copy; \n line breaks are preserved */
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm: () => void;
  /**
   * Omit for notice mode: a single acknowledge button is shown and
   * Escape / backdrop click invoke onConfirm instead.
   */
  onCancel?: () => void;
}

/**
 * In-fiction CRT confirmation dialog replacing native window.confirm/alert.
 * Rendered via portal so ancestor transforms/overflow never clip the overlay.
 * Events still bubble through the React tree from portals, so every handler
 * stops propagation to keep clickable parent cards from re-triggering.
 */
const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  onConfirm,
  onCancel
}) => {
  const confirmRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  // Mount timestamp: ignore overlay dismissals fired within 250ms of opening,
  // so the second click of a double-click on a card can't instantly close us.
  const mountedAtRef = useRef(performance.now());
  // Notice mode (no onCancel): dismissal acknowledges.
  const dismiss = onCancel ?? onConfirm;

  useEffect(() => {
    const previouslyFocused =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    confirmRef.current?.focus();
    return () => previouslyFocused?.focus();
  }, []);

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        dismiss();
      } else if (e.key === 'Tab') {
        // Focus trap: 2-element cycle between cancel and confirm (Tab and
        // Shift+Tab both swap); notice mode keeps focus on the lone button.
        e.preventDefault();
        if (cancelRef.current && confirmRef.current) {
          (document.activeElement === confirmRef.current
            ? cancelRef.current
            : confirmRef.current
          ).focus();
        } else {
          confirmRef.current?.focus();
        }
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [dismiss]);

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
        aria-label={title}
        onMouseDown={(e) => e.stopPropagation()}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="confirm-dialog-header">
          <span className="confirm-dialog-title">{title}</span>
        </div>
        <p className="confirm-dialog-message">{message}</p>
        <div className="confirm-dialog-actions">
          {onCancel && (
            <button
              ref={cancelRef}
              type="button"
              className="confirm-dialog-btn cancel"
              onClick={(e) => {
                e.stopPropagation();
                onCancel();
              }}
            >
              {cancelLabel}
            </button>
          )}
          <button
            ref={confirmRef}
            type="button"
            className="confirm-dialog-btn confirm"
            onClick={(e) => {
              e.stopPropagation();
              onConfirm();
            }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
};

export default ConfirmDialog;
