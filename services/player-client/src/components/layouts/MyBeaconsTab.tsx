import React, { useCallback, useEffect, useState } from 'react';
import { beaconAPI, type MyBeacon } from '../../services/api';
import { useGame } from '../../contexts/GameContext';
import EmptyState from '../common/EmptyState';

/**
 * MyBeaconsTab — the StatusBar dossier dropdown's "BEACONS" tab
 * (message-beacons.md § Player UX: "My Beacons screen shows all beacons
 * the player has deployed across the universe, with read counts and
 * salvage / expire links"). Same "compact roster in a fixed-size dropdown"
 * shape as ColoniesRosterTab/GovSummaryTab, but this one carries mutating
 * actions (deploy/read/salvage/recharge/report) since the dropdown is the ONLY
 * place a deployer manages beacons they aren't currently standing next to
 * — there is no separate full-page destination to link out to (unlike
 * Colonies/Governance, which hand off to a real console).
 *
 * WO-WIRE-MESSAGE-BEACON-DEPLOY: deploy form POSTs /beacons/deploy at the
 * player's current sector (5 turns + 500cr + 1 equipment).
 *
 * Read/salvage/report all require the ACTING player to be physically in
 * the beacon's sector server-side (message_beacon_service.read/salvage/
 * report's own same-sector anti-oracle 404 — see that file's docstrings);
 * a beacon deployed elsewhere in the universe will 404 those calls from
 * here. That 404 is indistinguishable from "beacon gone" by design
 * (anti-oracle), so this surfaces it as a location hint rather than a
 * hard error — the row itself is left untouched (it may still be there,
 * you're just not standing next to it). Recharge alone is remote-capable
 * (message_beacon_service.recharge's own docstring), matching the "owner
 * may recharge remotely from their My Beacons screen" canon line.
 */

type RowBusy = 'read' | 'salvage' | 'recharge' | 'report' | null;

/** Transport collapse copy is not gameserver detail (network-collapse densify). */
const isBeaconNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed) ||
    /^networkerror$/i.test(trimmed)
  );
};

function serverDetail(err: unknown): string | undefined {
  // Network collapse (fetch TypeError) is not gameserver copy — use the caller fallback.
  if (err instanceof TypeError) return undefined;
  if (err && typeof err === 'object') {
    const rawDetail = (err as { response?: { data?: { detail?: unknown } } }).response?.data
      ?.detail;
    if (typeof rawDetail === 'string' && rawDetail.trim()) return rawDetail.trim();
  }
  const message = err instanceof Error ? err.message : undefined;
  if (typeof message === 'string' && isBeaconNetworkCollapseMessage(message)) return undefined;
  if (
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim())
  ) {
    return message.trim();
  }
  return undefined;
}

export function formatBeaconDeployError(err: unknown): string {
  const detail = serverDetail(err);
  if (detail) return detail;
  return 'Deploy failed';
}

export function formatBeaconLoadError(err: unknown): string {
  const detail = serverDetail(err);
  if (detail) return detail;
  return 'Failed to load your beacons';
}

export function formatBeaconRowActionError(err: unknown): string {
  const detail = serverDetail(err);
  if (detail) return detail;
  return 'Action failed';
}

const formatState = (state: string): string => state.replace(/_/g, ' ');

const MyBeaconsTab: React.FC = () => {
  const { playerState, currentSector, refreshPlayerState } = useGame();
  const [beacons, setBeacons] = useState<MyBeacon[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<RowBusy>(null);
  const [rowMessages, setRowMessages] = useState<Record<string, string>>({});
  const [expandedMessage, setExpandedMessage] = useState<Record<string, string>>({});

  const [deployMessage, setDeployMessage] = useState('');
  const [deployReadOnce, setDeployReadOnce] = useState(false);
  const [deployBusy, setDeployBusy] = useState(false);
  const [deployFeedback, setDeployFeedback] = useState<string | null>(null);

  const sectorId =
    currentSector?.sector_id ??
    currentSector?.id ??
    playerState?.current_sector_id ??
    null;

  const load = useCallback(() => {
    let cancelled = false;
    beaconAPI
      .mine()
      .then((res) => {
        if (cancelled) return;
        setBeacons(res?.beacons || []);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(formatBeaconLoadError(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => load(), [load]);

  const setRowMessage = (id: string, msg: string) =>
    setRowMessages((prev) => ({ ...prev, [id]: msg }));

  const handleDeploy = async () => {
    if (sectorId == null || !deployMessage.trim() || deployBusy) return;
    setDeployBusy(true);
    setDeployFeedback(null);
    try {
      await beaconAPI.deploy({
        sector_id: Number(sectorId),
        message: deployMessage.trim(),
        read_once: deployReadOnce,
      });
      setDeployMessage('');
      setDeployReadOnce(false);
      setDeployFeedback('Beacon deployed.');
      load();
      try {
        await refreshPlayerState();
      } catch {
        /* deploy already succeeded */
      }
    } catch (err) {
      setDeployFeedback(formatBeaconDeployError(err));
    } finally {
      setDeployBusy(false);
    }
  };

  const runAction = async (beacon: MyBeacon, action: RowBusy) => {
    if (!action) return;
    setBusyId(beacon.id);
    setBusyAction(action);
    setRowMessage(beacon.id, '');
    try {
      if (action === 'read') {
        const result = await beaconAPI.read(beacon.id);
        setExpandedMessage((prev) => ({ ...prev, [beacon.id]: result.message }));
        if (beacon.read_once) {
          setBeacons((prev) => (prev ? prev.filter((b) => b.id !== beacon.id) : prev));
        } else {
          setBeacons((prev) =>
            prev
              ? prev.map((b) => (b.id === beacon.id ? { ...b, read_count: result.read_count ?? b.read_count } : b))
              : prev
          );
        }
      } else if (action === 'salvage') {
        await beaconAPI.salvage(beacon.id);
        setBeacons((prev) => (prev ? prev.filter((b) => b.id !== beacon.id) : prev));
      } else if (action === 'recharge') {
        const result = await beaconAPI.recharge(beacon.id);
        setBeacons((prev) =>
          prev
            ? prev.map((b) =>
                b.id === beacon.id
                  ? { ...b, charge_expires_at: result.charge_expires_at ?? b.charge_expires_at, state: result.state ?? b.state }
                  : b
              )
            : prev
        );
        setRowMessage(beacon.id, 'Recharged.');
      } else if (action === 'report') {
        await beaconAPI.report(beacon.id);
        setBeacons((prev) => (prev ? prev.filter((b) => b.id !== beacon.id) : prev));
      }
    } catch (err) {
      const msg = formatBeaconRowActionError(err);
      const notFound = /not found/i.test(msg);
      setRowMessage(
        beacon.id,
        notFound && action !== 'recharge'
          ? `Not in sector ${beacon.sector_id} right now — travel there to ${action}.`
          : msg
      );
    } finally {
      setBusyId(null);
      setBusyAction(null);
    }
  };

  const deployForm = (
    <div className="sb-beacons-deploy" data-testid="beacon-deploy-form">
      <div className="sb-beacons-deploy-title">
        Deploy here{sectorId != null ? ` · sector ${sectorId}` : ''}
      </div>
      <textarea
        className="sb-beacons-deploy-message"
        data-testid="beacon-deploy-message"
        maxLength={500}
        rows={2}
        placeholder="Message (≤500 chars) — costs 5 turns, ₡500, 1 equipment"
        value={deployMessage}
        onChange={(e) => setDeployMessage(e.target.value)}
        disabled={deployBusy || sectorId == null}
        aria-label="Beacon message"
      />
      <div className="sb-beacons-deploy-row">
        <label className="sb-beacons-deploy-once">
          <input
            type="checkbox"
            data-testid="beacon-deploy-read-once"
            checked={deployReadOnce}
            onChange={(e) => setDeployReadOnce(e.target.checked)}
            disabled={deployBusy}
          />
          Read-once
        </label>
        <button
          type="button"
          className="sb-beacons-deploy-btn"
          data-testid="beacon-deploy-submit"
          disabled={deployBusy || sectorId == null || !deployMessage.trim()}
          onClick={handleDeploy}
        >
          {deployBusy ? 'Deploying…' : 'Deploy'}
        </button>
      </div>
      {deployFeedback && (
        <div className="sb-beacons-row-message" role="status" data-testid="beacon-deploy-feedback">
          {deployFeedback}
        </div>
      )}
    </div>
  );

  if (error) {
    return <div className="sb-beacons-error" role="alert">{error}</div>;
  }

  if (beacons === null) {
    return (
      <div className="sb-beacons-loading" role="status" aria-live="polite">
        Loading…
      </div>
    );
  }

  if (beacons.length === 0) {
    return (
      <div className="sb-beacons-empty-with-deploy">
        {deployForm}
        <EmptyState
          icon="📡"
          title="No Beacons Deployed"
          message="Leave a note in this sector for other travelers (form above)."
        />
      </div>
    );
  }

  return (
    <div className="sb-beacons-roster">
      {deployForm}
      <ul className="sb-beacons-list">
        {beacons.map((b) => {
          const isBusy = busyId === b.id;
          return (
            <li key={b.id} className="sb-beacons-row">
              <div className="sb-beacons-header">
                <span className="sb-beacons-sector">SECTOR {b.sector_id}</span>
                <span className="sb-beacons-state">{formatState(b.state)}</span>
                {b.flagged && <span className="sb-beacons-flagged">FLAGGED</span>}
              </div>
              <div className="sb-beacons-preview">
                {expandedMessage[b.id] ?? `"${b.preview}"`}
              </div>
              <div className="sb-beacons-meta">
                <span>Reads: {b.read_count}</span>
                {b.read_once && <span>Read-once</span>}
                {b.charge_expires_at && (
                  <span>Charge until {new Date(b.charge_expires_at).toLocaleDateString()}</span>
                )}
              </div>
              <div className="sb-beacons-actions">
                <button
                  type="button"
                  disabled={isBusy}
                  onClick={() => runAction(b, 'read')}
                >
                  {isBusy && busyAction === 'read' ? '…' : 'Read'}
                </button>
                <button
                  type="button"
                  disabled={isBusy}
                  onClick={() => runAction(b, 'salvage')}
                >
                  {isBusy && busyAction === 'salvage' ? '…' : 'Salvage (250cr)'}
                </button>
                <button
                  type="button"
                  disabled={isBusy}
                  onClick={() => runAction(b, 'recharge')}
                >
                  {isBusy && busyAction === 'recharge' ? '…' : 'Recharge (200cr)'}
                </button>
                <button
                  type="button"
                  disabled={isBusy}
                  onClick={() => runAction(b, 'report')}
                  title="Flag this beacon for moderation review"
                >
                  {isBusy && busyAction === 'report' ? '…' : 'Report'}
                </button>
              </div>
              {rowMessages[b.id] && (
                <div className="sb-beacons-row-message" role="status">
                  {rowMessages[b.id]}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
};

export default MyBeaconsTab;
