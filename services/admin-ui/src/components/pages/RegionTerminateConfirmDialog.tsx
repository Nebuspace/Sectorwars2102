import React, { useEffect, useRef, useState } from 'react';

import { formatAdminApiError } from '../../utils/adminApiError';
import {
  fetchRegionTerminatePreview,
  type RegionTerminatePreview,
} from '../../services/regionTerminateApi';
import '../universe/bang/wipe-galaxy-confirm-dialog.css';

export function formatRegionTerminateError(err: unknown): string {
  if (err instanceof TypeError) {
    return 'Network error — could not reach the gameserver. Check your connection and try again.';
  }
  return formatAdminApiError(err, {
    fallback: 'Region termination failed',
    scopeHint: 'admin.regions.terminate scope required',
  });
}

interface RegionTerminateConfirmDialogProps {
  regionId: string;
  onCancel: () => void;
  onConfirm: (confirmRegionName: string, reason: string) => Promise<void>;
  busy?: boolean;
  error?: string | null;
}

/**
 * Multi-step confirmation per LEG-DEC-103: preview dependent-entity counts,
 * type exact region name, explicit acknowledge checkbox, mandatory reason.
 */
const RegionTerminateConfirmDialog: React.FC<RegionTerminateConfirmDialogProps> = ({
  regionId,
  onCancel,
  onConfirm,
  busy = false,
  error = null,
}) => {
  const [preview, setPreview] = useState<RegionTerminatePreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(true);
  const [typed, setTyped] = useState('');
  const [reason, setReason] = useState('');
  const [acknowledged, setAcknowledged] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setPreviewLoading(true);
      setPreviewError(null);
      try {
        const data = await fetchRegionTerminatePreview(regionId);
        if (!cancelled) {
          setPreview(data);
        }
      } catch (err) {
        if (!cancelled) {
          setPreviewError(formatRegionTerminateError(err));
        }
      } finally {
        if (!cancelled) {
          setPreviewLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [regionId]);

  useEffect(() => {
    if (preview && !previewLoading) {
      inputRef.current?.focus();
    }
  }, [preview, previewLoading]);

  const regionName = preview?.regionName ?? '';
  const nameMatches = typed === regionName;
  const reasonValid = reason.trim().length > 0;
  const canSubmit =
    Boolean(preview?.terminable) &&
    nameMatches &&
    reasonValid &&
    acknowledged &&
    !busy &&
    !previewLoading &&
    !previewError;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    await onConfirm(typed, reason.trim());
  };

  return (
    <div
      className="wipe-galaxy-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="region-terminate-title"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onCancel();
      }}
    >
      <div className="wipe-galaxy-panel">
        <div className="wipe-galaxy-header">
          <h2 id="region-terminate-title">Terminate Region</h2>
        </div>

        <form className="wipe-galaxy-body" onSubmit={handleSubmit}>
          {previewLoading && (
            <p className="wipe-galaxy-warning">Loading termination preview…</p>
          )}
          {previewError && (
            <p className="wipe-galaxy-error" role="alert">
              {previewError}
            </p>
          )}
          {preview && !previewLoading && (
            <>
              <p className="wipe-galaxy-warning">
                This permanently terminates <strong>{preview.displayName}</strong> (
                <code>{preview.regionName}</code>) and runs the full cleanup cascade.
                Planets: {preview.planetCount}, stations: {preview.stationCount}, sectors:{' '}
                {preview.sectorCount}, player stakeholders: {preview.playerStakeholderCount}.
              </p>
              {!preview.terminable && (
                <p className="wipe-galaxy-error" role="alert">
                  This region cannot be terminated (system region or already cleaned up).
                </p>
              )}
              <label className="wipe-galaxy-prompt" htmlFor="region-terminate-input">
                Type the region name exactly to confirm:
              </label>
              <input
                id="region-terminate-input"
                ref={inputRef}
                type="text"
                className="wipe-galaxy-input"
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                placeholder={regionName}
                autoComplete="off"
                spellCheck={false}
                disabled={busy || !preview.terminable}
              />
              {typed.length > 0 && !nameMatches && (
                <p className="wipe-galaxy-mismatch">Name does not match.</p>
              )}
              <label className="wipe-galaxy-prompt" htmlFor="region-terminate-reason">
                Reason (required — recorded in AdminActionLog):
              </label>
              <textarea
                id="region-terminate-reason"
                className="wipe-galaxy-input"
                rows={3}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                disabled={busy || !preview.terminable}
              />
              <label style={{ display: 'flex', gap: '8px', alignItems: 'flex-start' }}>
                <input
                  type="checkbox"
                  checked={acknowledged}
                  onChange={(e) => setAcknowledged(e.target.checked)}
                  disabled={busy || !preview.terminable}
                />
                <span>
                  I understand this action is irreversible and will remove dependent planets,
                  stations, and player stakes in this region.
                </span>
              </label>
            </>
          )}
          {error && (
            <p className="wipe-galaxy-error" role="alert">
              {error}
            </p>
          )}

          <div className="wipe-galaxy-actions">
            <button
              type="button"
              className="wipe-galaxy-cancel"
              onClick={onCancel}
              disabled={busy}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="wipe-galaxy-confirm"
              disabled={!canSubmit}
            >
              {busy ? 'Terminating…' : 'Terminate Region'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default RegionTerminateConfirmDialog;
