import React from 'react';
import { useGame } from '../../contexts/GameContext';
import {
  pirateHoldingsAPI,
  type PirateHoldingDiscovery,
} from '../../services/api';
import PirateHoldingCaptureControl from './PirateHoldingCaptureControl';

/** Transport collapse copy is not gameserver detail (network-collapse densify). */
const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed)
  );
};

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

export function formatPirateHoldingRaidError(error: unknown, fallback: string): string {
  if (error instanceof TypeError) return fallback;
  const status = httpStatus(error);
  const message = error instanceof Error ? error.message : undefined;
  const hasServerDetail =
    !(error instanceof TypeError) &&
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim()) &&
    !isNetworkCollapseMessage(message);

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'You do not have permission to initiate a raid on this holding.';
  }

  if (status === 429) {
    return 'Raid initiate rate limit exceeded — wait a moment and try again.';
  }

  if (error instanceof Error && error.message) {
    if (isNetworkCollapseMessage(error.message)) return fallback;
    return error.message;
  }
  return fallback;
}

function formatTierLabel(tier: string | null): string {
  if (!tier) return 'Unknown';
  const lower = tier.toLowerCase();
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}

/**
 * Pirate-holding raid initiate — GET discovery + POST .../raid/initiate (LEG-4107).
 * Mounted on the TACTICAL monitor THREAT page; disabled when docked or landed.
 * Capture UI mounts when lock_applied is true after initiate (LEG-4154).
 */
const PirateHoldingRaidControl: React.FC = () => {
  const { currentSector, playerState, refreshPlayerState } = useGame();
  const [busyId, setBusyId] = React.useState<string | null>(null);
  const [msg, setMsg] = React.useState<{ ok: boolean; text: string } | null>(null);
  const [holdings, setHoldings] = React.useState<PirateHoldingDiscovery[] | null>(null);
  const [activeLockHoldingId, setActiveLockHoldingId] = React.useState<string | null>(null);

  const inOpenSpace = !!playerState && !playerState.is_docked && !playerState.is_landed;
  const sectorId =
    currentSector && typeof currentSector.sector_id === 'number'
      ? currentSector.sector_id
      : null;

  const disabledReason = playerState?.is_docked
    ? 'Cannot initiate a pirate-holding raid while docked at a port'
    : playerState?.is_landed
      ? 'Cannot initiate a pirate-holding raid while landed on a planet'
      : null;

  const reloadHoldings = React.useCallback(async () => {
    if (!inOpenSpace || sectorId == null) {
      setHoldings([]);
      return;
    }
    try {
      const rows = await pirateHoldingsAPI.listBySector(sectorId);
      setHoldings(Array.isArray(rows) ? rows : []);
    } catch {
      setHoldings(null);
    }
  }, [inOpenSpace, sectorId]);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!inOpenSpace || sectorId == null) {
        if (!cancelled) setHoldings([]);
        return;
      }
      try {
        const rows = await pirateHoldingsAPI.listBySector(sectorId);
        if (!cancelled) setHoldings(Array.isArray(rows) ? rows : []);
      } catch {
        if (!cancelled) setHoldings(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [inOpenSpace, sectorId]);

  const hasHoldings = holdings !== null && holdings.length > 0;
  const showSection = inOpenSpace && (hasHoldings || msg !== null);

  const handleInitiate = async (holdingId: string) => {
    if (busyId || !inOpenSpace) return;
    setBusyId(holdingId);
    setMsg(null);
    try {
      const result = await pirateHoldingsAPI.initiateRaid(holdingId);
      const tier = formatTierLabel(result.tier);
      const lockNote = result.lock_applied
        ? ' Combat lock acquired.'
        : ' Camp entry — no combat lock required.';
      setMsg({
        ok: true,
        text: result.initiated
          ? `Raid initiated on ${tier} holding.${lockNote}`
          : 'Raid initiate returned without confirmation.',
      });
      if (result.lock_applied) {
        setActiveLockHoldingId(holdingId);
      }
      await refreshPlayerState();
      await reloadHoldings();
    } catch (e: unknown) {
      setMsg({
        ok: false,
        text: formatPirateHoldingRaidError(e, 'Pirate-holding raid initiate failed'),
      });
    } finally {
      setBusyId(null);
    }
  };

  if (!showSection) {
    if (!inOpenSpace && disabledReason) {
      return (
        <div className="threat-section" data-testid="pirate-holding-raid-control">
          <div className="threat-section-title" role="heading" aria-level={3}>
            PIRATE HOLDING RAID
          </div>
          <div className="threat-hint" role="status">
            {disabledReason}
          </div>
        </div>
      );
    }
    return null;
  }

  return (
    <div className="threat-section" data-testid="pirate-holding-raid-control">
      <div className="threat-section-title" role="heading" aria-level={3}>
        PIRATE HOLDING RAID
      </div>
      <p className="threat-hint">
        {hasHoldings
          ? `${holdings!.length} pirate holding${holdings!.length === 1 ? '' : 's'} in this sector. Initiate a raid to engage.`
          : 'Raid initiate complete.'}
      </p>
      {hasHoldings &&
        holdings!.map((h) => (
          <div className="threat-row" key={h.id}>
            <span className="threat-hint" data-testid={`pirate-holding-tier-${h.id}`}>
              {formatTierLabel(h.tier)}
            </span>
            <button
              type="button"
              className="threat-btn"
              data-testid={`pirate-holding-raid-initiate-${h.id}`}
              onClick={() => void handleInitiate(h.id)}
              disabled={busyId !== null}
              aria-busy={busyId === h.id}
              title={`Initiate raid on ${formatTierLabel(h.tier)} holding`}
            >
              {busyId === h.id ? '…' : 'INITIATE RAID ▸'}
            </button>
          </div>
        ))}
      {msg && (
        <div
          className={`threat-msg ${msg.ok ? 'ok' : 'err'}`}
          role="status"
          data-testid="pirate-holding-raid-msg"
        >
          {msg.text}
        </div>
      )}
      {activeLockHoldingId && (
        <PirateHoldingCaptureControl
          holdingId={activeLockHoldingId}
          onCaptured={() => {
            setActiveLockHoldingId(null);
            void reloadHoldings();
          }}
        />
      )}
    </div>
  );
};

export default PirateHoldingRaidControl;
