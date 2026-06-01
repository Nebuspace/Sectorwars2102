import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import './wipe-galaxy-confirm-dialog.css'; // reuse overlay + panel styles

interface AddRegionDialogProps {
  /** Called when the operator clicks cancel or the overlay. */
  onCancel: () => void;
  /** Called when the operator confirms; receives seed + sectors. */
  onConfirm: (seed: number, sectors: number) => Promise<void> | void;
  /** External in-flight flag (parent disables the submit button). */
  busy?: boolean;
  /** Optional error string surfaced from the parent. */
  error?: string | null;
}

/**
 * Lightweight dialog for the "Add Player-Owned Region" admin flow. Backend
 * (POST /admin/galaxy/{id}/regions) forces region_type to player_owned and
 * clamps sectors to [100, 1000] regardless of what the form sent — the
 * client-side validation here is just to make the input self-explanatory.
 */
const AddRegionDialog: React.FC<AddRegionDialogProps> = ({
  onCancel,
  onConfirm,
  busy = false,
  error = null,
}) => {
  const { t } = useTranslation('admin');
  const [seed, setSeed] = useState<number>(() =>
    Math.floor(Math.random() * Number.MAX_SAFE_INTEGER),
  );
  const [sectors, setSectors] = useState<number>(500);
  const seedInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    seedInputRef.current?.focus();
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    const clampedSectors = Math.max(100, Math.min(1000, Math.round(sectors)));
    await onConfirm(seed, clampedSectors);
  };

  return (
    <div
      className="wipe-galaxy-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="add-region-title"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onCancel();
      }}
    >
      <div className="wipe-galaxy-panel">
        <div className="wipe-galaxy-header">
          <h2 id="add-region-title">{t('bang.addRegion.title')}</h2>
        </div>

        <form className="wipe-galaxy-body" onSubmit={handleSubmit}>
          <p className="wipe-galaxy-warning">{t('bang.addRegion.description')}</p>

          <label className="wipe-galaxy-prompt" htmlFor="add-region-seed">
            {t('bang.addRegion.seed')}
          </label>
          <input
            id="add-region-seed"
            ref={seedInputRef}
            type="number"
            className="wipe-galaxy-input"
            value={seed}
            onChange={(e) => setSeed(Number(e.target.value))}
            min={0}
            required
            disabled={busy}
          />

          <label className="wipe-galaxy-prompt" htmlFor="add-region-sectors" style={{ marginTop: 12 }}>
            {t('bang.addRegion.sectors')}
          </label>
          <input
            id="add-region-sectors"
            type="number"
            className="wipe-galaxy-input"
            value={sectors}
            onChange={(e) => setSectors(Number(e.target.value))}
            min={100}
            max={1000}
            required
            disabled={busy}
          />

          {error && <p className="wipe-galaxy-error">{error}</p>}

          <div className="wipe-galaxy-actions">
            <button
              type="button"
              className="wipe-galaxy-cancel"
              onClick={onCancel}
              disabled={busy}
            >
              {t('bang.addRegion.cancel')}
            </button>
            <button
              type="submit"
              className="wipe-galaxy-confirm"
              disabled={busy}
            >
              {busy ? t('bang.addRegion.busy') : t('bang.addRegion.submit')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default AddRegionDialog;
