import React from 'react';
import { useGame } from '../../contexts/GameContext';
import {
  combatAPI,
  warpGatesAPI,
  type AttackWarpGateResponse,
  type AttackWarpGateTarget,
} from '../../services/api';

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

export function formatWarpGateAttackError(error: unknown, fallback: string): string {
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
    return 'You do not have permission to attack this warp gate.';
  }

  if (status === 429) {
    return 'Warp gate attack rate limit exceeded — wait a moment and try again.';
  }

  if (error instanceof Error && error.message) {
    if (isNetworkCollapseMessage(error.message)) return fallback;
    return error.message;
  }
  return fallback;
}

type SectorGateRow = {
  gate_id: string;
  destination_sector_id?: number;
  destination_name?: string | null;
  owner_name?: string | null;
};

type SectorBeaconRow = {
  beacon_id: string;
  destination_sector_id?: number;
  destination_name?: string | null;
  owner_name?: string | null;
};

function parseSectorStructures(data: unknown): {
  gates: SectorGateRow[];
  beacons: SectorBeaconRow[];
} {
  const body =
    data && typeof data === 'object' ? (data as { gates?: unknown; beacons?: unknown }) : {};
  const gates = Array.isArray(body.gates)
    ? body.gates
        .filter(
          (g): g is SectorGateRow =>
            !!g && typeof g === 'object' && typeof (g as SectorGateRow).gate_id === 'string',
        )
        .map((g) => g as SectorGateRow)
    : [];
  const beacons = Array.isArray(body.beacons)
    ? body.beacons
        .filter(
          (b): b is SectorBeaconRow =>
            !!b &&
            typeof b === 'object' &&
            typeof (b as { beacon_id?: unknown }).beacon_id === 'string',
        )
        .map((b) => b as SectorBeaconRow)
    : [];
  return { gates, beacons };
}

function formatAttackSuccess(result: AttackWarpGateResponse): string {
  const base = result.message || 'Warp gate attack resolved.';
  if (result.destroyed) {
    const salvage =
      result.salvageGranted && typeof result.salvageGranted === 'object'
        ? ' Salvage recovered.'
        : '';
    return `${base} Structure destroyed.${salvage}`;
  }
  if (typeof result.gateHpRemaining === 'number') {
    return `${base} Gate HP remaining: ${result.gateHpRemaining}.`;
  }
  return base;
}

/**
 * Warp-gate / Phase-1 beacon attack — POST /combat/attack-warp-gate (LEG-4116).
 * Mounted on the TACTICAL monitor THREAT page; disabled when docked or landed.
 * Invent=0: attack/salvage path only — no built-in defenses or upgrades.
 */
const WarpGateAttackControl: React.FC = () => {
  const { currentSector, playerState, refreshPlayerState, getAvailableMoves } = useGame();
  const [busyId, setBusyId] = React.useState<string | null>(null);
  const [msg, setMsg] = React.useState<{ ok: boolean; text: string } | null>(null);
  const [gates, setGates] = React.useState<SectorGateRow[] | null>(null);
  const [beacons, setBeacons] = React.useState<SectorBeaconRow[] | null>(null);

  const inOpenSpace = !!playerState && !playerState.is_docked && !playerState.is_landed;
  const sectorId =
    currentSector && typeof currentSector.sector_id === 'number'
      ? currentSector.sector_id
      : null;

  const disabledReason = playerState?.is_docked
    ? 'Cannot attack warp gates while docked at a port'
    : playerState?.is_landed
      ? 'Cannot attack warp gates while landed on a planet'
      : null;

  const reloadStructures = React.useCallback(async () => {
    if (!inOpenSpace || sectorId == null) {
      setGates([]);
      setBeacons([]);
      return;
    }
    try {
      const data = await warpGatesAPI.listSector(sectorId);
      const parsed = parseSectorStructures(data);
      setGates(parsed.gates);
      setBeacons(parsed.beacons);
    } catch {
      setGates(null);
      setBeacons(null);
    }
  }, [inOpenSpace, sectorId]);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!inOpenSpace || sectorId == null) {
        if (!cancelled) {
          setGates([]);
          setBeacons([]);
        }
        return;
      }
      try {
        const data = await warpGatesAPI.listSector(sectorId);
        if (!cancelled) {
          const parsed = parseSectorStructures(data);
          setGates(parsed.gates);
          setBeacons(parsed.beacons);
        }
      } catch {
        if (!cancelled) {
          setGates(null);
          setBeacons(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [inOpenSpace, sectorId]);

  const gateList = gates ?? [];
  const beaconList = beacons ?? [];
  const hasTargets = gates !== null && beacons !== null && (gateList.length > 0 || beaconList.length > 0);
  const showSection = inOpenSpace && (hasTargets || msg !== null);

  const handleAttack = async (target: AttackWarpGateTarget, busyKey: string) => {
    if (busyId || !inOpenSpace) return;
    setBusyId(busyKey);
    setMsg(null);
    try {
      const result = await combatAPI.attackWarpGate(target);
      if (result.success) {
        setMsg({ ok: true, text: formatAttackSuccess(result) });
        await refreshPlayerState();
        if (getAvailableMoves) {
          await getAvailableMoves();
        }
        await reloadStructures();
      } else {
        setMsg({ ok: false, text: result.message || 'Warp gate attack failed.' });
      }
    } catch (e: unknown) {
      setMsg({
        ok: false,
        text: formatWarpGateAttackError(e, 'Warp gate attack failed'),
      });
    } finally {
      setBusyId(null);
    }
  };

  if (!showSection) {
    if (!inOpenSpace && disabledReason) {
      return (
        <div className="threat-section" data-testid="warp-gate-attack-control">
          <div className="threat-section-title" role="heading" aria-level={3}>
            WARP GATE ATTACK
          </div>
          <div className="threat-hint" role="status">
            {disabledReason}
          </div>
        </div>
      );
    }
    return null;
  }

  const targetCount = gateList.length + beaconList.length;

  return (
    <div className="threat-section" data-testid="warp-gate-attack-control">
      <div className="threat-section-title" role="heading" aria-level={3}>
        WARP GATE ATTACK
      </div>
      <p className="threat-hint">
        {hasTargets
          ? `${targetCount} warp structure${targetCount === 1 ? '' : 's'} in this sector. Attack to damage or destroy.`
          : 'Warp gate engagement complete.'}
      </p>
      {hasTargets &&
        gateList.map((g) => {
          const dest =
            typeof g.destination_sector_id === 'number'
              ? `→ ${g.destination_name || `Sector ${g.destination_sector_id}`}`
              : 'Gate';
          const owner = g.owner_name ? ` (${g.owner_name})` : '';
          return (
            <div className="threat-row" key={`gate-${g.gate_id}`}>
              <span className="threat-hint" data-testid={`warp-gate-label-${g.gate_id}`}>
                Gate {dest}
                {owner}
              </span>
              <button
                type="button"
                className="threat-btn"
                data-testid={`warp-gate-attack-${g.gate_id}`}
                onClick={() => void handleAttack({ gateId: g.gate_id }, `gate-${g.gate_id}`)}
                disabled={busyId !== null}
                aria-busy={busyId === `gate-${g.gate_id}`}
                title="Attack this warp gate"
              >
                {busyId === `gate-${g.gate_id}` ? '…' : 'ATTACK GATE ▸'}
              </button>
            </div>
          );
        })}
      {hasTargets &&
        beaconList.map((b) => {
          const dest =
            typeof b.destination_sector_id === 'number'
              ? `→ ${b.destination_name || `Sector ${b.destination_sector_id}`}`
              : 'Beacon';
          const owner = b.owner_name ? ` (${b.owner_name})` : '';
          return (
            <div className="threat-row" key={`beacon-${b.beacon_id}`}>
              <span className="threat-hint" data-testid={`warp-beacon-label-${b.beacon_id}`}>
                Beacon {dest}
                {owner}
              </span>
              <button
                type="button"
                className="threat-btn"
                data-testid={`warp-beacon-attack-${b.beacon_id}`}
                onClick={() => void handleAttack({ beaconId: b.beacon_id }, `beacon-${b.beacon_id}`)}
                disabled={busyId !== null}
                aria-busy={busyId === `beacon-${b.beacon_id}`}
                title="Attack this Phase-1 warp beacon"
              >
                {busyId === `beacon-${b.beacon_id}` ? '…' : 'ATTACK BEACON ▸'}
              </button>
            </div>
          );
        })}
      {msg && (
        <div
          className={`threat-msg ${msg.ok ? 'ok' : 'err'}`}
          role="status"
          data-testid="warp-gate-attack-msg"
        >
          {msg.text}
        </div>
      )}
    </div>
  );
};

export default WarpGateAttackControl;
