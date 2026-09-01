import React, { useState } from 'react';
import { shipRegistryAPI } from '../../services/api';
import type { SectorContact } from '../tactical/contactClassification';
import './salvage-break-cta.css';

export interface SalvageBreakInProgress {
  ship_id: string;
  completes_at?: string;
  eta_hours?: number;
}

/** Transport collapse copy is not gameserver detail (network-collapse densify). */
const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed)
  );
};

/** Surface GS salvage-break detail; hide fetch TypeError / network-collapse noise (LEG-3144 / LEG-3298). */
export function formatSalvageBreakError(err: unknown): string {
  const transportFallback = 'Salvage break failed — check your connection and try again.';
  if (err instanceof TypeError) {
    return transportFallback;
  }

  let message = err instanceof Error ? err.message : undefined;
  if (typeof message === 'string' && isNetworkCollapseMessage(message)) {
    return transportFallback;
  }

  let code: string | undefined;
  if (err && typeof err === 'object') {
    const typed = err as { code?: unknown; data?: { detail?: unknown } };
    if (typeof typed.code === 'string' && typed.code.trim()) {
      code = typed.code.trim();
    }
    const detail = typed.data?.detail;
    if (typeof detail === 'string' && detail.trim()) {
      message = detail.trim();
    } else if (detail && typeof detail === 'object') {
      const structured = detail as { message?: unknown; code?: unknown; completes_at?: unknown };
      if (typeof structured.message === 'string' && structured.message.trim()) {
        message = structured.message.trim();
      }
      if (typeof structured.code === 'string' && structured.code.trim()) {
        code = structured.code.trim();
      }
      if (code === 'ERR_SALVAGE_BREAK_IN_PROGRESS') {
        const eta =
          typeof structured.completes_at === 'string' && structured.completes_at.trim()
            ? ` ETA ${new Date(structured.completes_at).toLocaleTimeString()}.`
            : '';
        return `Salvage break already in progress on this hull.${eta}`;
      }
    }
  }

  if (code === 'ERR_SALVAGE_BREAK_IN_PROGRESS') {
    return 'Salvage break already in progress on this hull.';
  }

  if (message && !(err instanceof TypeError)) {
    if (code && !message.includes(code)) return `${message} [${code}]`;
    return message;
  }
  return 'Salvage break failed';
}

/** Drifting pin-locked hull in-sector — contact may carry enrichment flags. */
export function isSalvageBreakEligibleContact(
  contact: SectorContact,
  selfPlayerId?: string | null,
): boolean {
  if (!contact.ship_id) return false;
  if (contact.is_npc) return false;

  const pid = contact.player_id || contact.user_id || contact.id;
  if (selfPlayerId && pid && String(pid) === String(selfPlayerId)) return false;

  const shipType = String(contact.ship_type || contact.shipType || '').toUpperCase();
  if (shipType.includes('ESCAPE_POD')) return false;

  const pinLocked =
    contact.pin_locked === true ||
    contact.hatch_pin_locked === true ||
    contact.hatch_pin_set === true ||
    (typeof contact.hatch_pin_code === 'string' && contact.hatch_pin_code.length > 0);

  const drifting =
    contact.is_drifting === true ||
    contact.drifting === true ||
    (!contact.player_id && !contact.user_id);

  return drifting && pinLocked;
}

function formatEta(completesAt?: string): string | null {
  if (!completesAt) return null;
  const ms = Date.parse(completesAt);
  if (Number.isNaN(ms)) return null;
  const sec = Math.max(0, Math.round((ms - Date.now()) / 1000));
  if (sec <= 0) return 'completing…';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export interface SalvageBreakCtaProps {
  shipId: string;
  shipName?: string;
  inProgress?: SalvageBreakInProgress | null;
  disabled?: boolean;
  onDone?: () => void;
}

const SalvageBreakCta: React.FC<SalvageBreakCtaProps> = ({
  shipId,
  shipName,
  inProgress,
  disabled,
  onDone,
}) => {
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null);
  const [localCompletesAt, setLocalCompletesAt] = useState<string | null>(null);

  const completesAt = inProgress?.completes_at || localCompletesAt;
  const eta = formatEta(completesAt ?? undefined);

  const run = async () => {
    if (busy || disabled) return;
    const label = shipName ? `"${shipName}"` : 'this hull';
    if (!window.confirm(`Start salvage break on ${label}? You must stay in-sector until it completes.`)) return;
    setBusy(true);
    setFeedback(null);
    try {
      const result = (await shipRegistryAPI.salvageBreak(shipId)) as { completes_at?: string };
      if (result?.completes_at) setLocalCompletesAt(result.completes_at);
      setFeedback({ ok: true, text: 'Salvage break started.' });
      onDone?.();
    } catch (err: unknown) {
      setFeedback({ ok: false, text: formatSalvageBreakError(err) });
    } finally {
      setBusy(false);
    }
  };

  if (inProgress || localCompletesAt) {
    return (
      <div className="salvage-break-cta in-progress" data-testid="salvage-break-in-progress">
        <span className="salvage-break-label">SALVAGE BREAK</span>
        <span className="salvage-break-eta" role="status">
          {eta ? `ETA ${eta}` : 'In progress…'}
        </span>
      </div>
    );
  }

  return (
    <div className="salvage-break-cta" data-testid="salvage-break-cta">
      <button
        type="button"
        className="salvage-break-btn"
        disabled={busy || disabled}
        aria-busy={busy}
        onClick={() => { void run(); }}
      >
        {busy ? '…' : 'SALVAGE BREAK ▸'}
      </button>
      {feedback && (
        <div className={`salvage-break-msg ${feedback.ok ? 'ok' : 'err'}`} role="alert">
          {feedback.text}
        </div>
      )}
    </div>
  );
};

export default SalvageBreakCta;
