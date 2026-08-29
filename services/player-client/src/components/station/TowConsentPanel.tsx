import React, { useCallback, useEffect, useState } from 'react';
import { towAPI, type TowStatus } from '../../services/api';
import { useSectorContacts } from '../tactical/contactClassification';
import './tow-consent-panel.css';

function serverDetail(err: unknown): string | undefined {
  if (err && typeof err === 'object') {
    const rawDetail = (err as { response?: { data?: { detail?: unknown } } }).response?.data
      ?.detail;
    if (typeof rawDetail === 'string' && rawDetail.trim()) return rawDetail.trim();
  }
  const message = err instanceof Error ? err.message : undefined;
  if (
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim())
  ) {
    return message.trim();
  }
  return undefined;
}

export function formatTowActionError(err: unknown): string {
  const detail = serverDetail(err);
  if (detail) return detail;
  return 'Tow action failed';
}

/**
 * TowConsentPanel — WO-WIRE-TOW-CONSENT-UI.
 *
 * Polls GET /tow/status. Surfaces:
 * - pending_incoming → Accept / Decline (cancel)
 * - pending_outgoing → Cancel request
 * - active tow (towing / being_towed_by) → Detach
 * - otherwise → Request tow against sector contacts with a ship_id
 */
const TowConsentPanel: React.FC = () => {
  const contacts = useSectorContacts();
  const [status, setStatus] = useState<TowStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [openRequest, setOpenRequest] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const next = await towAPI.getStatus();
      setStatus(next);
    } catch {
      /* keep prior status; panel is best-effort */
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const next = await towAPI.getStatus();
        if (!cancelled) setStatus(next);
      } catch {
        if (!cancelled) setStatus(null);
      }
    })();
    const id = window.setInterval(() => {
      if (!cancelled) void refresh();
    }, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [refresh]);

  const run = async (fn: () => Promise<unknown>, okMsg: string) => {
    if (busy) return;
    setBusy(true);
    setFeedback(null);
    try {
      await fn();
      setFeedback(okMsg);
      await refresh();
    } catch (err: unknown) {
      setFeedback(formatTowActionError(err));
    } finally {
      setBusy(false);
    }
  };

  if (!status) return null;

  const incoming = status.pending_incoming;
  const outgoing = status.pending_outgoing;
  const active = Boolean(status.towing || status.being_towed_by);
  const towTargets = contacts.filter((c) => Boolean(c.ship_id));

  if (!incoming && !outgoing && !active && !openRequest) {
    if (towTargets.length === 0) return null;
    return (
      <div className="tow-consent-rail" data-testid="tow-consent-rail">
        <button
          type="button"
          data-testid="tow-consent-open-request"
          disabled={busy}
          onClick={() => setOpenRequest(true)}
        >
          Request tow…
        </button>
        {feedback && (
          <p className="tow-consent-feedback" role="status">
            {feedback}
          </p>
        )}
      </div>
    );
  }

  return (
    <div
      className="tow-consent-panel"
      role="dialog"
      aria-labelledby="tow-consent-title"
      data-testid="tow-consent-panel"
    >
      <div className="tow-consent-card">
        <h3 id="tow-consent-title">Tractor tow</h3>

        {incoming && (
          <div className="tow-consent-block" data-testid="tow-consent-incoming">
            <p>
              Incoming tow request from hauler <code>{incoming.hauler_id.slice(0, 8)}…</code>
              {incoming.surcharge_per_move != null
                ? ` (+${incoming.surcharge_per_move} turns/move surcharge on hauler).`
                : '.'}{' '}
              Expires in ~60s if ignored.
            </p>
            <div className="tow-consent-actions">
              <button
                type="button"
                data-testid="tow-consent-accept"
                disabled={busy}
                onClick={() =>
                  run(() => towAPI.accept(incoming.hauler_id), 'Tow accepted — you are locked.')
                }
              >
                Accept
              </button>
              <button
                type="button"
                data-testid="tow-consent-decline"
                disabled={busy}
                onClick={() =>
                  run(() => towAPI.cancel(incoming.hauler_id), 'Tow request declined.')
                }
              >
                Decline
              </button>
            </div>
          </div>
        )}

        {outgoing && !incoming && (
          <div className="tow-consent-block" data-testid="tow-consent-outgoing">
            <p>
              Waiting for target to accept tow
              {outgoing.towed_ship_id
                ? ` (${outgoing.towed_ship_id.slice(0, 8)}…)`
                : ''}
              …
            </p>
            <button
              type="button"
              data-testid="tow-consent-cancel-outgoing"
              disabled={busy}
              onClick={() =>
                run(() => towAPI.cancel(outgoing.hauler_id), 'Tow request cancelled.')
              }
            >
              Cancel request
            </button>
          </div>
        )}

        {active && !incoming && (
          <div className="tow-consent-block" data-testid="tow-consent-active">
            <p>
              {status.towing
                ? 'You are towing another ship.'
                : 'You are being towed.'}{' '}
              Detach is free (0 turns), including from combat.
            </p>
            <button
              type="button"
              data-testid="tow-consent-detach"
              disabled={busy}
              onClick={() => run(() => towAPI.detach(), 'Tow detached.')}
            >
              Detach
            </button>
          </div>
        )}

        {openRequest && !incoming && !outgoing && !active && (
          <div className="tow-consent-block" data-testid="tow-consent-request-list">
            <p>Request a tow lock on a ship in this sector (they must Accept):</p>
            <ul className="tow-consent-targets">
              {towTargets.map((c) => {
                const shipId = String(c.ship_id);
                const label = c.username || c.name || shipId.slice(0, 8);
                return (
                  <li key={shipId}>
                    <span>{label}</span>
                    <button
                      type="button"
                      data-testid={`tow-consent-request-${shipId}`}
                      disabled={busy}
                      onClick={() =>
                        run(async () => {
                          await towAPI.request(shipId);
                          setOpenRequest(false);
                        }, 'Tow request sent — waiting for Accept.')
                      }
                    >
                      Request
                    </button>
                  </li>
                );
              })}
            </ul>
            <button type="button" disabled={busy} onClick={() => setOpenRequest(false)}>
              Close
            </button>
          </div>
        )}

        {feedback && (
          <p className="tow-consent-feedback" role="status" data-testid="tow-consent-feedback">
            {feedback}
          </p>
        )}
      </div>
    </div>
  );
};

export default TowConsentPanel;
