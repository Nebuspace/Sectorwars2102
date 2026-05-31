import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import './wipe-galaxy-confirm-dialog.css';

interface WipeGalaxyConfirmDialogProps {
  /** Galaxy display name — operator must type this exactly to confirm. */
  galaxyName: string;
  /** Called when the operator clicks the cancel button or the overlay. */
  onCancel: () => void;
  /** Called when the operator confirms; receives the typed name for the header. */
  onConfirm: (confirmName: string) => Promise<void> | void;
  /** External in-flight flag (parent disables the confirm button). */
  busy?: boolean;
  /** Optional error string surfaced from the parent. */
  error?: string | null;
}

/**
 * Typed-name confirmation modal. This is the project's first such modal —
 * the existing codebase uses `window.confirm` for destructive ops. The
 * confirm button is disabled until the typed string equals `galaxyName`
 * exactly (case-sensitive, no whitespace trimming). The same typed string
 * is forwarded to the API call so the gameserver can echo-verify via the
 * `X-Confirm-Galaxy-Name` header.
 */
const WipeGalaxyConfirmDialog: React.FC<WipeGalaxyConfirmDialogProps> = ({
  galaxyName,
  onCancel,
  onConfirm,
  busy = false,
  error = null,
}) => {
  const { t } = useTranslation('admin');
  const [typed, setTyped] = useState('');
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    // Focus the input on mount for keyboard-only operators.
    inputRef.current?.focus();
  }, []);

  const matches = typed === galaxyName;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!matches || busy) return;
    await onConfirm(typed);
  };

  return (
    <div
      className="wipe-galaxy-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="wipe-galaxy-title"
      onClick={(e) => {
        // Only close on overlay (not inner panel) clicks, and not while busy.
        if (e.target === e.currentTarget && !busy) onCancel();
      }}
    >
      <div className="wipe-galaxy-panel">
        <div className="wipe-galaxy-header">
          <h2 id="wipe-galaxy-title">{t('bang.wipe.title')}</h2>
        </div>

        <form className="wipe-galaxy-body" onSubmit={handleSubmit}>
          <p className="wipe-galaxy-warning">
            {t('bang.wipe.warning', { galaxyName })}
          </p>
          <label className="wipe-galaxy-prompt" htmlFor="wipe-galaxy-input">
            {t('bang.wipe.prompt')}
          </label>
          <input
            id="wipe-galaxy-input"
            ref={inputRef}
            type="text"
            className="wipe-galaxy-input"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={galaxyName}
            autoComplete="off"
            spellCheck={false}
            disabled={busy}
          />
          {typed.length > 0 && !matches && (
            <p className="wipe-galaxy-mismatch">{t('bang.wipe.mismatch')}</p>
          )}
          {error && <p className="wipe-galaxy-error">{error}</p>}

          <div className="wipe-galaxy-actions">
            <button
              type="button"
              className="wipe-galaxy-cancel"
              onClick={onCancel}
              disabled={busy}
            >
              {t('bang.wipe.cancel')}
            </button>
            <button
              type="submit"
              className="wipe-galaxy-confirm"
              disabled={!matches || busy}
            >
              {busy ? t('bang.wipe.wiping') : t('bang.wipe.confirm')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default WipeGalaxyConfirmDialog;
