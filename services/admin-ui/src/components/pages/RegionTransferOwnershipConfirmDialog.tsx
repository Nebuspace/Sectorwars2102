import React, { useEffect, useRef, useState } from 'react';

import { formatAdminApiError } from '../../utils/adminApiError';
import '../universe/bang/wipe-galaxy-confirm-dialog.css';

const GAMESERVER_UNREACHABLE =
  'Network error — could not reach the gameserver. Check your connection and try again.';

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function isTransportCollapse(err: unknown): boolean {
  if (err instanceof TypeError) return true;
  if (!(err instanceof Error)) return false;
  const msg = err.message.trim();
  return msg === '' || msg === 'Network Error';
}

export function formatRegionTransferError(err: unknown): string {
  if (isTransportCollapse(err)) {
    return GAMESERVER_UNREACHABLE;
  }
  return formatAdminApiError(err, {
    fallback: 'Region ownership transfer failed',
    scopeHint: 'admin.regions.transfer_ownership scope required',
  });
}

export function isValidOwnerUuid(value: string): boolean {
  return UUID_RE.test(value.trim());
}

interface RegionTransferOwnershipConfirmDialogProps {
  regionId: string;
  regionDisplayName: string;
  currentOwnerId?: string;
  onCancel: () => void;
  onConfirm: (newOwnerId: string, reason: string) => Promise<void>;
  busy?: boolean;
  error?: string | null;
}

/**
 * Admin unilateral region ownership transfer (LEG-DEC-500): new owner UUID +
 * mandatory reason; audit-logged server-side. No recipient-consent step.
 */
const RegionTransferOwnershipConfirmDialog: React.FC<
  RegionTransferOwnershipConfirmDialogProps
> = ({
  regionDisplayName,
  currentOwnerId,
  onCancel,
  onConfirm,
  busy = false,
  error = null,
}) => {
  const [newOwnerId, setNewOwnerId] = useState('');
  const [reason, setReason] = useState('');
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const ownerIdValid = isValidOwnerUuid(newOwnerId);
  const reasonValid = reason.trim().length > 0;
  const canSubmit = ownerIdValid && reasonValid && !busy;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    await onConfirm(newOwnerId.trim(), reason.trim());
  };

  return (
    <div
      className="wipe-galaxy-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="region-transfer-title"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onCancel();
      }}
    >
      <div className="wipe-galaxy-panel">
        <div className="wipe-galaxy-header">
          <h2 id="region-transfer-title">Transfer Region Ownership</h2>
        </div>

        <form className="wipe-galaxy-body" onSubmit={handleSubmit}>
          <p className="wipe-galaxy-warning">
            Transfer ownership of <strong>{regionDisplayName}</strong> to another user.
            This is a unilateral admin action — the new owner is not required to accept.
            {currentOwnerId && (
              <>
                {' '}
                Current owner: <code>{currentOwnerId}</code>.
              </>
            )}
          </p>

          <label className="wipe-galaxy-prompt" htmlFor="region-transfer-owner-id">
            New owner user UUID:
          </label>
          <input
            id="region-transfer-owner-id"
            ref={inputRef}
            type="text"
            className="wipe-galaxy-input"
            value={newOwnerId}
            onChange={(e) => setNewOwnerId(e.target.value)}
            placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
            autoComplete="off"
            spellCheck={false}
            disabled={busy}
          />
          {newOwnerId.length > 0 && !ownerIdValid && (
            <p className="wipe-galaxy-mismatch">Enter a valid user UUID.</p>
          )}

          <label className="wipe-galaxy-prompt" htmlFor="region-transfer-reason">
            Reason (required — recorded in AdminActionLog):
          </label>
          <textarea
            id="region-transfer-reason"
            className="wipe-galaxy-input"
            rows={3}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            disabled={busy}
          />

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
            <button type="submit" className="wipe-galaxy-confirm" disabled={!canSubmit}>
              {busy ? 'Transferring…' : 'Transfer Ownership'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default RegionTransferOwnershipConfirmDialog;
