import React from 'react';
import { useGame } from '../../contexts/GameContext';
import { combatAPI, droneFleetAPI } from '../../services/api';

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

export function formatSectorDroneAttackError(error: unknown, fallback: string): string {
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
    return 'You do not have permission to attack sector drones.';
  }

  if (status === 429) {
    return 'Sector drone combat rate limit exceeded — wait a moment and try again.';
  }

  if (error instanceof Error && error.message) {
    if (isNetworkCollapseMessage(error.message)) return fallback;
    return error.message;
  }
  return fallback;
}

const ACTIVE_DRONE_STATUSES = new Set(['deployed', 'damaged']);

function countHostileSectorDrones(
  drones: unknown,
  playerId: string | undefined,
): number {
  if (!Array.isArray(drones) || !playerId) return 0;
  return drones.filter((row) => {
    if (!row || typeof row !== 'object') return false;
    const d = row as { player_id?: string; status?: string; health?: number };
    if (String(d.player_id) === String(playerId)) return false;
    const status = typeof d.status === 'string' ? d.status.toLowerCase() : '';
    if (!ACTIVE_DRONE_STATUSES.has(status)) return false;
    return typeof d.health === 'number' ? d.health > 0 : true;
  }).length;
}

/**
 * Sector drone combat — POST /combat/attack-sector-drones (LEG-3968).
 * Mounted on the TACTICAL monitor THREAT page; disabled when docked or landed.
 */
const SectorDroneAttackControl: React.FC = () => {
  const { currentSector, playerState, refreshPlayerState, getAvailableMoves } = useGame();
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState<{ ok: boolean; text: string } | null>(null);
  const [hostileCount, setHostileCount] = React.useState<number | null>(null);

  const inOpenSpace = !!playerState && !playerState.is_docked && !playerState.is_landed;
  const sectorUuid = currentSector?.id ? String(currentSector.id) : null;
  const playerId = playerState?.id ? String(playerState.id) : undefined;

  const disabledReason = playerState?.is_docked
    ? 'Cannot attack sector drones while docked at a port'
    : playerState?.is_landed
      ? 'Cannot attack sector drones while landed on a planet'
      : null;

  React.useEffect(() => {
    if (!inOpenSpace || !sectorUuid || !playerId) {
      setHostileCount(0);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const drones = await droneFleetAPI.getSectorDrones(sectorUuid);
        if (!cancelled) {
          setHostileCount(countHostileSectorDrones(drones, playerId));
        }
      } catch {
        if (!cancelled) setHostileCount(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [inOpenSpace, sectorUuid, playerId]);

  const hasHostiles = hostileCount !== null && hostileCount > 0;
  const showSection = inOpenSpace && (hasHostiles || msg !== null);

  const handleAttack = async () => {
    if (busy || !inOpenSpace) return;
    setBusy(true);
    setMsg(null);
    try {
      const result = await combatAPI.attackSectorDrones();
      if (result.success) {
        const destroyed =
          typeof result.dronesDestroyed === 'number'
            ? ` Destroyed ${result.dronesDestroyed} drone(s).`
            : '';
        const turns =
          typeof result.turnsConsumed === 'number'
            ? ` (${result.turnsConsumed} turn(s) spent.)`
            : '';
        setMsg({
          ok: true,
          text: `${result.message || 'Sector drones cleared.'}${destroyed}${turns}`,
        });
        await refreshPlayerState();
        if (getAvailableMoves) {
          await getAvailableMoves();
        }
        if (sectorUuid && playerId) {
          try {
            const drones = await droneFleetAPI.getSectorDrones(sectorUuid);
            setHostileCount(countHostileSectorDrones(drones, playerId));
          } catch {
            setHostileCount(0);
          }
        }
      } else {
        setMsg({ ok: false, text: result.message || 'Sector drone attack failed.' });
      }
    } catch (e: unknown) {
      setMsg({ ok: false, text: formatSectorDroneAttackError(e, 'Sector drone attack failed') });
    } finally {
      setBusy(false);
    }
  };

  if (!showSection) {
    if (!inOpenSpace && disabledReason) {
      return (
        <div className="threat-section" data-testid="sector-drone-attack-control">
          <div className="threat-section-title" role="heading" aria-level={3}>
            SECTOR DRONE COMBAT
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
    <div className="threat-section" data-testid="sector-drone-attack-control">
      <div className="threat-section-title" role="heading" aria-level={3}>
        SECTOR DRONE COMBAT
      </div>
      <p className="threat-hint">
        {hasHostiles
          ? `${hostileCount} hostile drone${hostileCount === 1 ? '' : 's'} detected in this sector. Clear them in a 2-turn engagement.`
          : 'Sector drone engagement complete.'}
      </p>
      {hasHostiles && (
        <div className="threat-row">
          <button
            type="button"
            className="threat-btn"
            data-testid="sector-drone-attack-clear"
            onClick={() => void handleAttack()}
            disabled={busy}
            aria-busy={busy}
            title="Attack hostile sector drones (2 turns)"
          >
            {busy ? '…' : 'CLEAR SECTOR DRONES ▸'}
          </button>
        </div>
      )}
      {msg && (
        <div
          className={`threat-msg ${msg.ok ? 'ok' : 'err'}`}
          role="status"
          data-testid="sector-drone-attack-msg"
        >
          {msg.text}
        </div>
      )}
    </div>
  );
};

export default SectorDroneAttackControl;
