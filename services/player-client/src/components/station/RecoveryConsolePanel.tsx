import React, { useCallback, useEffect, useState } from 'react';
import { recoveryAPI, type RecoveryStatus } from '../../services/api';
import { useGame } from '../../contexts/GameContext';
import './recovery-console-panel.css';

/**
 * RecoveryConsolePanel — WO-WIRE-RECOVERY-CONSOLE.
 *
 * Wires GET /api/v1/recovery/status + distress / slipdrive / escape-pod
 * POSTs. Collapsed rail by default; expands to the stranding recovery desk.
 */
const RecoveryConsolePanel: React.FC = () => {
  const { refreshPlayerState, loadShips } = useGame();
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<RecoveryStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await recoveryAPI.getStatus();
      setStatus(next);
    } catch {
      /* best-effort */
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const next = await recoveryAPI.getStatus();
        if (!cancelled) setStatus(next);
      } catch {
        if (!cancelled) setStatus(null);
      }
    })();
    if (!open) {
      return () => {
        cancelled = true;
      };
    }
    const id = window.setInterval(() => {
      if (!cancelled) void refresh();
    }, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [open, refresh]);

  const afterAction = async (okMsg: string) => {
    setFeedback(okMsg);
    await refresh();
    await Promise.allSettled([refreshPlayerState(), loadShips()]);
  };

  const run = async (fn: () => Promise<unknown>, okMsg: string) => {
    if (busy) return;
    setBusy(true);
    setFeedback(null);
    try {
      await fn();
      await afterAction(okMsg);
    } catch (err: unknown) {
      setFeedback(err instanceof Error ? err.message : 'Recovery action failed');
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <div className="recovery-console-rail" data-testid="recovery-console-rail">
        <button
          type="button"
          data-testid="recovery-console-open"
          onClick={() => setOpen(true)}
        >
          Recovery…
        </button>
      </div>
    );
  }

  const distress = status?.distress_beacon;
  const slip = status?.slipdrive;

  return (
    <div
      className="recovery-console-panel"
      role="dialog"
      aria-labelledby="recovery-console-title"
      data-testid="recovery-console-panel"
    >
      <div className="recovery-console-card">
        <div className="recovery-console-header">
          <h3 id="recovery-console-title">Stranding recovery</h3>
          <button
            type="button"
            className="recovery-console-close"
            data-testid="recovery-console-close"
            onClick={() => setOpen(false)}
          >
            Close
          </button>
        </div>

        <section className="recovery-console-section" aria-label="Distress beacon">
          <h4>Federation distress beacon</h4>
          <p>
            Free teleport to nearest fedspace. −10 Terran Federation reputation · 24h
            cooldown.
          </p>
          {distress?.available === false && distress.cooldown_until ? (
            <p className="recovery-console-meta" data-testid="recovery-distress-cooldown">
              Recharging until {new Date(distress.cooldown_until).toLocaleString()}
            </p>
          ) : null}
          <button
            type="button"
            data-testid="recovery-distress-fire"
            disabled={busy || distress?.available === false}
            onClick={() =>
              run(
                () => recoveryAPI.fireDistressBeacon(),
                'Distress beacon fired — relocating to fedspace.'
              )
            }
          >
            Fire distress beacon
          </button>
        </section>

        <section className="recovery-console-section" aria-label="Slipdrive">
          <h4>Warp Jumper Slipdrive</h4>
          <p>Multi-turn charge, then teleport to nearest non-sink sector (fuel cost).</p>
          {slip?.charging && slip.charge_deadline ? (
            <p className="recovery-console-meta" data-testid="recovery-slipdrive-deadline">
              {slip.ready
                ? 'Charge ready.'
                : `Charging until ${new Date(slip.charge_deadline).toLocaleString()}`}
            </p>
          ) : null}
          {slip?.cancelled_by_movement ? (
            <p className="recovery-console-meta">Charge cancelled — you moved mid-charge.</p>
          ) : null}
          <div className="recovery-console-actions">
            <button
              type="button"
              data-testid="recovery-slipdrive-begin"
              disabled={busy || Boolean(slip?.charging && !slip?.cancelled_by_movement)}
              onClick={() =>
                run(() => recoveryAPI.beginSlipdrive(), 'Slipdrive charge started.')
              }
            >
              Begin charge
            </button>
            <button
              type="button"
              data-testid="recovery-slipdrive-complete"
              disabled={busy || !slip?.ready}
              onClick={() =>
                run(() => recoveryAPI.completeSlipdrive(), 'Slipdrive jump complete.')
              }
            >
              Complete jump
            </button>
          </div>
        </section>

        <section className="recovery-console-section" aria-label="Escape pod">
          <h4>Escape pod</h4>
          <p>Abandon current hull — free teleport to nearest non-sink sector.</p>
          <button
            type="button"
            data-testid="recovery-escape-pod"
            disabled={busy}
            onClick={() => {
              const ok = window.confirm(
                'Eject to escape pod? Your current ship will be abandoned.'
              );
              if (!ok) return;
              void run(() => recoveryAPI.escapePod(), 'Ejected to escape pod.');
            }}
          >
            Eject to escape pod
          </button>
        </section>

        {feedback ? (
          <p className="recovery-console-feedback" role="status">
            {feedback}
          </p>
        ) : null}
      </div>
    </div>
  );
};

export default RecoveryConsolePanel;
