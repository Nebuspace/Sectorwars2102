import React, { useState } from 'react';
import { useGame } from '../../contexts/GameContext';
import { stationSecurityAPI } from '../../services/api';
import './tractor-lock-prompt.css';

/**
 * TractorLockPrompt — WO-WIRE-TRACTOR-LOCK-SURRENDER-UI.
 *
 * When undock hits ERR_STATION_TRACTOR_LOCK, GameContext stores the payload
 * and this modal offers Break free / Surrender (Fight = ordinary combat once
 * security squads exist). Mounted once in GameLayout.
 */

/** Transport collapse copy is not gameserver detail (network-collapse densify). */
const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed) ||
    /^networkerror$/i.test(trimmed)
  );
};

/** Normalize GS/API detail from apiRequest Error.message, axios-shaped response, or object detail. */
function tractorLockServerDetail(err: unknown): string | undefined {
  // Network collapse (fetch TypeError / axios Network Error) is not gameserver copy.
  if (err instanceof TypeError) return undefined;

  if (err && typeof err === 'object') {
    const rawDetail =
      (err as { response?: { data?: { detail?: unknown } } }).response?.data?.detail ??
      (err as { data?: { detail?: unknown } }).data?.detail;
    if (typeof rawDetail === 'string' && rawDetail.trim()) return rawDetail.trim();
    if (rawDetail && typeof rawDetail === 'object' && !Array.isArray(rawDetail)) {
      const nested = (rawDetail as { message?: unknown }).message;
      if (typeof nested === 'string' && nested.trim()) return nested.trim();
      try {
        return JSON.stringify(rawDetail);
      } catch {
        /* fall through to Error.message */
      }
    }
  }
  const message = err instanceof Error ? err.message : undefined;
  if (typeof message === 'string' && isNetworkCollapseMessage(message)) return undefined;
  if (
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim())
  ) {
    return message.trim();
  }
  return undefined;
}

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

/** Surface gameserver break/surrender refusal detail. */
export function formatTractorLockActionError(err: unknown): string {
  const status = httpStatus(err);
  const detail = tractorLockServerDetail(err);

  if (status === 403) {
    if (detail) return detail;
    return 'You do not have permission to resolve this tractor lock.';
  }

  if (status === 429) {
    return 'Tractor lock action rate limit exceeded — wait a moment and try again.';
  }

  return detail ?? 'Action failed';
}

const TractorLockPrompt: React.FC = () => {
  const { tractorLock, clearTractorLock, refreshPlayerState } = useGame();
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  if (!tractorLock) return null;

  const run = async (action: 'break' | 'surrender') => {
    setBusy(true);
    setFeedback(null);
    try {
      if (action === 'break') {
        const result = await stationSecurityAPI.breakTractorLock(tractorLock.station_id);
        setFeedback(
          typeof result?.message === 'string'
            ? result.message
            : result?.outcome === 'escaped' || result?.success === true
              ? 'Tractor beam broken — you are free.'
              : 'Break attempt spent. Still locked.'
        );
        if (result?.outcome === 'escaped' || result?.success === true) {
          clearTractorLock();
          await refreshPlayerState();
        }
      } else {
        const ok = window.confirm(
          'Surrender this ship to station security? You will pay a cargo-value fine, take a reputation hit, and reseat into an Escape Pod.'
        );
        if (!ok) {
          setBusy(false);
          return;
        }
        await stationSecurityAPI.surrenderTractorLock(tractorLock.station_id);
        clearTractorLock();
        await refreshPlayerState();
        setFeedback('Ship surrendered. You are reseated in an Escape Pod.');
      }
    } catch (err: unknown) {
      setFeedback(formatTractorLockActionError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="tractor-lock-prompt"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="tractor-lock-title"
      data-testid="tractor-lock-prompt"
    >
      <div className="tractor-lock-prompt-card">
        <h3 id="tractor-lock-title">Tractor lock engaged</h3>
        <p className="tractor-lock-reason">
          Station security has locked your ship
          {tractorLock.reason ? ` (${tractorLock.reason.replace(/_/g, ' ')})` : ''}.
          Strength: {tractorLock.tractor_strength || 'unknown'}. Break cost:{' '}
          {tractorLock.break_attempt_cost || 'turns'}.
        </p>
        <div className="tractor-lock-actions">
          <button
            type="button"
            data-testid="tractor-lock-break"
            disabled={busy}
            onClick={() => run('break')}
          >
            Break free
          </button>
          <button
            type="button"
            data-testid="tractor-lock-surrender"
            className="danger"
            disabled={busy}
            onClick={() => run('surrender')}
          >
            Surrender ship
          </button>
          <button type="button" disabled={busy} onClick={() => clearTractorLock()}>
            Dismiss
          </button>
        </div>
        {feedback && (
          <p className="tractor-lock-feedback" role="status" data-testid="tractor-lock-feedback">
            {feedback}
          </p>
        )}
      </div>
    </div>
  );
};

export default TractorLockPrompt;
