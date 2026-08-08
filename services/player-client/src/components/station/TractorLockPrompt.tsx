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
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (err instanceof Error ? err.message : 'Action failed');
      setFeedback(typeof detail === 'string' ? detail : JSON.stringify(detail));
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
