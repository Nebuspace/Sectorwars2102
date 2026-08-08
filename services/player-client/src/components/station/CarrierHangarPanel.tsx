import React, { useCallback, useEffect, useState } from 'react';
import { hangarAPI, type HangarStatus } from '../../services/api';
import { useSectorContacts } from '../tactical/contactClassification';
import { useGame } from '../../contexts/GameContext';
import './carrier-hangar-panel.css';

/**
 * CarrierHangarPanel — WO-WIRE-CARRIER-HANGAR-UI.
 *
 * Polls GET /hangar/status. Surfaces captain Accept/Cancel, passenger
 * Undock/Disembark, and Request dock against CARRIER contacts in-sector.
 */
const CarrierHangarPanel: React.FC = () => {
  const contacts = useSectorContacts();
  const { refreshPlayerState } = useGame();
  const [status, setStatus] = useState<HangarStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [openRequest, setOpenRequest] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setStatus(await hangarAPI.getStatus());
    } catch {
      /* best-effort */
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const next = await hangarAPI.getStatus();
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
      await refreshPlayerState();
    } catch (err: unknown) {
      setFeedback(err instanceof Error ? err.message : 'Hangar action failed');
    } finally {
      setBusy(false);
    }
  };

  if (!status) return null;

  const pendingEntries = (status.owned_carrier?.docked || []).filter(
    (e) => e.request_state === 'PENDING'
  );
  const carriers = contacts.filter((c) => {
    const t = String(c.ship_type || c.shipType || c.type || '').toUpperCase();
    return t === 'CARRIER' && Boolean(c.ship_id);
  });

  const hasCaptainWork = Boolean(status.owned_carrier && pendingEntries.length > 0);
  const hasPassenger = Boolean(status.hangared_on);
  const hasOutgoing = Boolean(status.pending_outgoing);
  const idleRail = !hasCaptainWork && !hasPassenger && !hasOutgoing && !openRequest;

  if (idleRail) {
    if (carriers.length === 0 && !status.owned_carrier) return null;
    return (
      <div className="carrier-hangar-rail" data-testid="carrier-hangar-rail">
        {carriers.length > 0 && (
          <button
            type="button"
            data-testid="carrier-hangar-open-request"
            disabled={busy}
            onClick={() => setOpenRequest(true)}
          >
            Request hangar dock…
          </button>
        )}
        {status.owned_carrier && (
          <span className="carrier-hangar-cap" data-testid="carrier-hangar-cap">
            Hangar {status.owned_carrier.used_units}/{status.owned_carrier.capacity_units}
          </span>
        )}
        {feedback && <p className="carrier-hangar-feedback">{feedback}</p>}
      </div>
    );
  }

  return (
    <div
      className="carrier-hangar-panel"
      role="dialog"
      aria-labelledby="carrier-hangar-title"
      data-testid="carrier-hangar-panel"
    >
      <div className="carrier-hangar-card">
        <h3 id="carrier-hangar-title">Carrier hangar</h3>

        {hasPassenger && status.hangared_on && (
          <div className="carrier-hangar-block" data-testid="carrier-hangar-passenger">
            <p>
              You are hangared on carrier{' '}
              <code>{status.hangared_on.carrier_id.slice(0, 8)}…</code>. Undock costs 1 turn;
              disembark to a port is free when the Carrier is docked.
            </p>
            <div className="carrier-hangar-actions">
              <button
                type="button"
                data-testid="carrier-hangar-undock"
                disabled={busy}
                onClick={() => run(() => hangarAPI.undock(), 'Undocked — you have the helm.')}
              >
                Undock (1 turn)
              </button>
              <button
                type="button"
                data-testid="carrier-hangar-disembark"
                disabled={busy}
                onClick={() => run(() => hangarAPI.disembark(), 'Disembarked to port.')}
              >
                Disembark
              </button>
            </div>
          </div>
        )}

        {hasOutgoing && status.pending_outgoing && (
          <div className="carrier-hangar-block" data-testid="carrier-hangar-outgoing">
            <p>Waiting for Carrier captain to accept your dock request…</p>
            <button
              type="button"
              data-testid="carrier-hangar-cancel-outgoing"
              disabled={busy}
              onClick={() =>
                run(
                  () =>
                    hangarAPI.cancel(
                      status.pending_outgoing!.carrier_id,
                      String(status.pending_outgoing!.ship_id)
                    ),
                  'Dock request cancelled.'
                )
              }
            >
              Cancel request
            </button>
          </div>
        )}

        {hasCaptainWork && status.owned_carrier && (
          <div className="carrier-hangar-block" data-testid="carrier-hangar-captain">
            <p>
              Pending dock requests ({status.owned_carrier.used_units}/
              {status.owned_carrier.capacity_units} units used):
            </p>
            <ul className="carrier-hangar-pending">
              {pendingEntries.map((e) => {
                const shipId = String(e.ship_id);
                return (
                  <li key={shipId}>
                    <span>
                      {shipId.slice(0, 8)}… · {String(e.size_units ?? '?')}u
                    </span>
                    <span className="carrier-hangar-actions">
                      <button
                        type="button"
                        data-testid={`carrier-hangar-accept-${shipId}`}
                        disabled={busy}
                        onClick={() =>
                          run(
                            () => hangarAPI.accept(status.owned_carrier!.carrier_id, shipId),
                            'Dock accepted.'
                          )
                        }
                      >
                        Accept
                      </button>
                      <button
                        type="button"
                        data-testid={`carrier-hangar-decline-${shipId}`}
                        disabled={busy}
                        onClick={() =>
                          run(
                            () => hangarAPI.cancel(status.owned_carrier!.carrier_id, shipId),
                            'Dock request declined.'
                          )
                        }
                      >
                        Decline
                      </button>
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        {openRequest && (
          <div className="carrier-hangar-block" data-testid="carrier-hangar-request-list">
            <p>Request a hangar slot on a Carrier in this sector:</p>
            <ul className="carrier-hangar-pending">
              {carriers.map((c) => {
                const id = String(c.ship_id);
                const label = c.username || c.name || id.slice(0, 8);
                return (
                  <li key={id}>
                    <span>{label}</span>
                    <button
                      type="button"
                      data-testid={`carrier-hangar-request-${id}`}
                      disabled={busy}
                      onClick={() =>
                        run(async () => {
                          await hangarAPI.requestDock(id);
                          setOpenRequest(false);
                        }, 'Dock request sent.')
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
          <p className="carrier-hangar-feedback" role="status" data-testid="carrier-hangar-feedback">
            {feedback}
          </p>
        )}
      </div>
    </div>
  );
};

export default CarrierHangarPanel;
