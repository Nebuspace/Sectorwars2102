import React, { useCallback, useEffect, useState } from 'react';
import { useGame } from '../../contexts/GameContext';
import { portOwnershipAPI } from '../../services/api';

type TeamOwnershipStatus = {
  station_id: string;
  mode: string;
  team_id: string | null;
  member_share_pct: number | null;
  owner_id: string | null;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed)
  );
};

/** Same densify contract as formatPortOfficeVenueError (403/429/TypeError). */
export function formatTeamOwnershipError(error: unknown, fallback: string): string {
  if (error instanceof TypeError) return fallback;
  const e = asRecord(error);
  const response = asRecord(e?.response);
  const status =
    (typeof e?.status === 'number' ? e.status : undefined) ??
    (typeof response?.status === 'number' ? response.status : undefined);
  const data = asRecord(response?.data);
  const raw = data?.message ?? data?.detail;
  let detailCopy: string | undefined;
  if (typeof raw === 'string' && raw.trim()) detailCopy = raw.trim();
  const message = typeof e?.message === 'string' ? e.message : undefined;
  const messageDetail =
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim()) &&
    !isNetworkCollapseMessage(message)
      ? message.trim()
      : undefined;
  const serverCopy = detailCopy ?? messageDetail;

  if (status === 403) {
    if (serverCopy) return serverCopy;
    return 'You do not have permission to change team ownership settings.';
  }
  if (status === 429) {
    return 'Team ownership action rate limit exceeded — wait a moment and try again.';
  }
  if (detailCopy) return detailCopy;
  if (!response && messageDetail) return messageDetail;
  if (message && isNetworkCollapseMessage(message)) return fallback;
  return fallback;
}

function parseStatus(raw: unknown): TeamOwnershipStatus | null {
  const body = asRecord(raw);
  if (!body) return null;
  const pctRaw = body.member_share_pct;
  let member_share_pct: number | null = null;
  if (typeof pctRaw === 'number' && Number.isFinite(pctRaw)) {
    member_share_pct = pctRaw;
  } else if (pctRaw !== null && pctRaw !== undefined) {
    const n = Number(pctRaw);
    if (Number.isFinite(n)) member_share_pct = n;
  }
  return {
    station_id: typeof body.station_id === 'string' ? body.station_id : '',
    mode: typeof body.mode === 'string' ? body.mode : 'solo',
    team_id: typeof body.team_id === 'string' ? body.team_id : null,
    member_share_pct,
    owner_id: typeof body.owner_id === 'string' ? body.owner_id : null,
  };
}

export interface PortOfficeTeamPanelProps {
  stationId: string;
  stationName: string;
}

/**
 * Port Office team ownership — tip GS /team* (LEG-4120).
 * invent=0: bind solo→team + member-share pct only.
 */
const PortOfficeTeamPanel: React.FC<PortOfficeTeamPanelProps> = ({
  stationId,
  stationName,
}) => {
  const { playerState, refreshPlayerState } = useGame();
  const playerTeamId = playerState?.team_id ? String(playerState.team_id) : '';

  const [status, setStatus] = useState<TeamOwnershipStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [bindTeamId, setBindTeamId] = useState(playerTeamId);
  const [bindSharePct, setBindSharePct] = useState(10);
  const [sharePct, setSharePct] = useState(10);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const raw = await portOwnershipAPI.getTeamOwnershipStatus(stationId);
      const parsed = parseStatus(raw);
      setStatus(parsed);
      if (parsed?.member_share_pct !== null && parsed?.member_share_pct !== undefined) {
        setSharePct(parsed.member_share_pct);
      }
    } catch (e: unknown) {
      setStatus(null);
      setError(
        formatTeamOwnershipError(e, 'Team ownership feed is down. Please try again.'),
      );
    } finally {
      setLoading(false);
    }
  }, [stationId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    if (playerTeamId && !bindTeamId) setBindTeamId(playerTeamId);
  }, [playerTeamId, bindTeamId]);

  const isTeamMode = status?.mode === 'team' && !!status.team_id;
  const isSolo = !isTeamMode;

  const run = async (key: string, action: () => Promise<unknown>, okText: string) => {
    if (busy) return;
    setBusy(key);
    setMsg(null);
    try {
      const result = await action();
      const body = asRecord(result);
      const serverMsg = typeof body?.message === 'string' ? body.message : okText;
      setMsg({ ok: true, text: serverMsg });
      if (refreshPlayerState) await refreshPlayerState();
      await reload();
    } catch (e: unknown) {
      setMsg({
        ok: false,
        text: formatTeamOwnershipError(e, 'Team ownership action failed. Please try again.'),
      });
    } finally {
      setBusy(null);
    }
  };

  if (loading && !status) {
    return (
      <div className="po-section" data-testid="po-team-panel">
        <h3 className="po-section-title">👥 Team Ownership</h3>
        <div className="catalog-loading">Loading team ownership...</div>
      </div>
    );
  }

  if (error && !status) {
    return (
      <div className="po-section" data-testid="po-team-panel">
        <h3 className="po-section-title">👥 Team Ownership</h3>
        <div className="genesis-error-message" role="alert">
          <span className="error-icon">❌</span>
          {error}
          <button type="button" className="action-button" onClick={() => void reload()}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!status) return null;

  return (
    <div className="po-section" data-testid="po-team-panel">
      <h3 className="po-section-title">👥 Team Ownership</h3>
      <p className="section-description">
        Bind {stationName} to a team so LEADER/OFFICER can set the member revenue share.
        Server enforces role gates; this console only posts tip Port Authority routes.
      </p>

      <div className="po-tariff-current" data-testid="po-team-status">
        Mode: <strong>{status.mode}</strong>
        {status.team_id ? (
          <>
            {' '}
            · Team <code>{status.team_id}</code>
          </>
        ) : null}
        {status.member_share_pct !== null && status.member_share_pct !== undefined ? (
          <> · Member share {status.member_share_pct}%</>
        ) : null}
      </div>

      {msg && (
        <div
          className={msg.ok ? 'genesis-success-message' : 'genesis-error-message'}
          role={msg.ok ? 'status' : 'alert'}
          data-testid="po-team-msg"
        >
          <span className={msg.ok ? 'success-icon' : 'error-icon'}>{msg.ok ? '✅' : '❌'}</span>
          {msg.text}
        </div>
      )}

      {isSolo && (
        <div className="po-defense-grid" data-testid="po-team-bind">
          <label className="po-defense-field">
            <span>Team id (UUID)</span>
            <input
              type="text"
              value={bindTeamId}
              disabled={Boolean(busy)}
              aria-label="Team id to bind"
              data-testid="po-team-bind-id"
              onChange={(e) => setBindTeamId(e.target.value.trim())}
              placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
            />
          </label>
          <label className="po-defense-field">
            <span>Member share ({bindSharePct}%)</span>
            <input
              type="range"
              min={0}
              max={100}
              step={1}
              value={bindSharePct}
              disabled={Boolean(busy)}
              aria-label="Initial member share percent"
              data-testid="po-team-bind-share"
              onChange={(e) => setBindSharePct(parseInt(e.target.value, 10) || 0)}
            />
          </label>
          <button
            className="action-button primary"
            type="button"
            data-testid="po-team-bind-submit"
            disabled={Boolean(busy) || !bindTeamId}
            onClick={() =>
              void run(
                'bind',
                () => portOwnershipAPI.bindStationToTeam(stationId, bindTeamId, bindSharePct),
                `Station bound to team ${bindTeamId}.`,
              )
            }
          >
            {busy === 'bind' ? 'Binding...' : 'Bind to Team'}
          </button>
        </div>
      )}

      {isTeamMode && (
        <div className="po-defense-grid" data-testid="po-team-member-share">
          <label className="po-defense-field">
            <span>Member revenue share ({sharePct}%)</span>
            <input
              type="range"
              min={0}
              max={100}
              step={1}
              value={sharePct}
              disabled={Boolean(busy)}
              aria-label="Member revenue share percent"
              data-testid="po-team-share-pct"
              onChange={(e) => setSharePct(parseInt(e.target.value, 10) || 0)}
            />
          </label>
          <button
            className="action-button primary"
            type="button"
            data-testid="po-team-share-submit"
            disabled={Boolean(busy)}
            onClick={() =>
              void run(
                'share',
                () => portOwnershipAPI.setTeamMemberShare(stationId, sharePct),
                `Member share set to ${sharePct}%.`,
              )
            }
          >
            {busy === 'share' ? 'Posting...' : 'Post Member Share'}
          </button>
        </div>
      )}
    </div>
  );
};

export default PortOfficeTeamPanel;
