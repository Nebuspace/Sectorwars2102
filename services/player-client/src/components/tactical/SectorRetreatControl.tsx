import React from 'react';
import { useGame } from '../../contexts/GameContext';
import { combatAPI } from '../../services/api';

/** Transport collapse copy is not gameserver detail (network-collapse densify). */
const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed)
  );
};

export function formatSectorRetreatError(error: unknown, fallback: string): string {
  if (error instanceof TypeError) return fallback;
  if (error instanceof Error && error.message) {
    if (isNetworkCollapseMessage(error.message)) return fallback;
    return error.message;
  }
  return fallback;
}

/**
 * Sector flee control — POST /combat/retreat (LEG-3107).
 * Mounted on the TACTICAL monitor THREAT page; disabled when docked or landed.
 */
const SectorRetreatControl: React.FC = () => {
  const { playerState, refreshPlayerState, getAvailableMoves } = useGame();
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState<{ ok: boolean; text: string } | null>(null);

  const inOpenSpace = !!playerState && !playerState.is_docked && !playerState.is_landed;
  const disabledReason = playerState?.is_docked
    ? 'Cannot flee while docked at a port'
    : playerState?.is_landed
      ? 'Cannot flee while landed on a planet'
      : null;

  const handleRetreat = async () => {
    if (busy || !inOpenSpace) return;
    setBusy(true);
    setMsg(null);
    try {
      const result = await combatAPI.retreatFromSector();
      if (result.success) {
        setMsg({
          ok: true,
          text:
            result.message ||
            (result.newSectorId != null
              ? `Escaped to sector ${result.newSectorId}.`
              : 'Sector retreat succeeded.'),
        });
        await refreshPlayerState();
        if (getAvailableMoves) {
          await getAvailableMoves();
        }
      } else {
        const chance =
          typeof result.escapeChance === 'number' ? ` (${result.escapeChance}% escape roll failed)` : '';
        setMsg({ ok: false, text: `${result.message || 'Retreat failed.'}${chance}` });
        if (typeof result.turnsRemaining === 'number') {
          await refreshPlayerState();
        }
      }
    } catch (e: unknown) {
      setMsg({ ok: false, text: formatSectorRetreatError(e, 'Sector retreat failed') });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="threat-section" data-testid="sector-retreat-control">
      <div className="threat-section-title" role="heading" aria-level={3}>
        SECTOR RETREAT
      </div>
      <p className="threat-hint">
        Attempt an emergency warp to a random connected sector. Costs 3 turns regardless of outcome.
      </p>
      {!inOpenSpace ? (
        <div className="threat-hint" role="status">
          {disabledReason}
        </div>
      ) : (
        <div className="threat-row">
          <button
            type="button"
            className="threat-btn"
            data-testid="sector-retreat-flee"
            onClick={() => void handleRetreat()}
            disabled={busy}
            aria-busy={busy}
            title="Flee to a connected sector (3 turns)"
          >
            {busy ? '…' : 'FLEE SECTOR ▸'}
          </button>
        </div>
      )}
      {msg && (
        <div className={`threat-msg ${msg.ok ? 'ok' : 'err'}`} role="status" data-testid="sector-retreat-msg">
          {msg.text}
        </div>
      )}
    </div>
  );
};

export default SectorRetreatControl;
