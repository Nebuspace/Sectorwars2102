import React, { useEffect, useRef, useState } from 'react';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { useGame } from '../../contexts/GameContext';
import pendingEngagementApi, {
  PendingEngagementSummary,
} from '../../services/pendingEngagementApi';
import { formatPoliceEnRouteMessage } from './formatPoliceEnRouteMessage';
import './police-en-route-banner.css';

const POLICE_EN_ROUTE_LOAD_FALLBACK = 'Failed to load law enforcement status';

/** Transport collapse copy is not gameserver detail (LEG-3713 densify). */
const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed) ||
    /^networkerror$/i.test(trimmed)
  );
};

/** Exported for TypeError/network honesty Vitest (LEG-3713). */
function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

export function formatPoliceEnRouteLoadError(err: unknown): string {
  if (err instanceof TypeError) return POLICE_EN_ROUTE_LOAD_FALLBACK;
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  const hasServerDetail =
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim()) &&
    !isNetworkCollapseMessage(message);

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'You do not have permission to load law enforcement status.';
  }

  if (status === 429) {
    return 'Law enforcement status rate limit exceeded — wait a moment and try again.';
  }

  if (hasServerDetail) return message!;
  return POLICE_EN_ROUTE_LOAD_FALLBACK;
}

function engagementKey(summary: PendingEngagementSummary, index: number): string {
  return summary.id || `idx-${index}`;
}

/**
 * PoliceEnRouteBanner — countdown HUD for inbound LAW enforcement (LEG-902).
 * Server owns turns_to_arrival (GET /pending-engagements + WS `police_en_route`).
 * Clears when the list is empty or LAW npc_combat_initiated arrives for this player.
 */
const PoliceEnRouteBanner: React.FC = () => {
  const {
    isConnected,
    policeEnRouteSignal,
    lastPoliceEnRoute,
    npcCombatSignal,
    lastNpcCombatInitiated,
  } = useWebSocket();
  const { playerState } = useGame();

  const [items, setItems] = useState<PendingEngagementSummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [dismissedKeys, setDismissedKeys] = useState<Set<string>>(new Set());
  const seenPoliceSignal = useRef(0);
  const seenNpcCombatSignal = useRef(0);

  useEffect(() => {
    if (!playerState?.id || !isConnected) {
      return;
    }

    let cancelled = false;
    pendingEngagementApi
      .listMine()
      .then((list) => {
        if (!cancelled) {
          setItems(list);
          setLoadError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setItems([]);
          setLoadError(formatPoliceEnRouteLoadError(err));
        }
      });

    return () => {
      cancelled = true;
    };
  }, [playerState?.id, isConnected]);

  useEffect(() => {
    if (
      policeEnRouteSignal <= 0 ||
      policeEnRouteSignal === seenPoliceSignal.current ||
      !lastPoliceEnRoute
    ) {
      return;
    }
    seenPoliceSignal.current = policeEnRouteSignal;

    setItems((prev) => {
      const id = lastPoliceEnRoute.id;
      if (!id) {
        return [lastPoliceEnRoute];
      }
      const without = prev.filter((row) => row.id !== id);
      return [...without, lastPoliceEnRoute];
    });

    if (lastPoliceEnRoute.id) {
      setDismissedKeys((prev) => {
        const next = new Set(prev);
        next.delete(lastPoliceEnRoute.id);
        return next;
      });
    }
  }, [policeEnRouteSignal, lastPoliceEnRoute]);

  useEffect(() => {
    if (
      npcCombatSignal <= 0 ||
      npcCombatSignal === seenNpcCombatSignal.current ||
      !lastNpcCombatInitiated ||
      !playerState
    ) {
      return;
    }
    seenNpcCombatSignal.current = npcCombatSignal;

    if (
      lastNpcCombatInitiated.npc_archetype === 'LAW_ENFORCEMENT' &&
      lastNpcCombatInitiated.defender_id === playerState.id
    ) {
      setItems([]);
      setDismissedKeys(new Set());
    }
  }, [npcCombatSignal, lastNpcCombatInitiated, playerState]);

  const active =
    items
      .map((row, index) => ({ row, key: engagementKey(row, index) }))
      .filter(({ key }) => !dismissedKeys.has(key))
      .sort((a, b) => a.row.turns_to_arrival - b.row.turns_to_arrival)[0] ??
    null;

  if (!active) {
    if (!loadError) {
      return null;
    }
    return (
      <div className="police-en-route-banner police-en-route-banner--error" role="alert">
        <div>
          <div className="police-en-route-banner-label">Law enforcement en route</div>
          <div className="police-en-route-banner-message">{loadError}</div>
        </div>
      </div>
    );
  }

  const { row, key } = active;
  const message = formatPoliceEnRouteMessage(row);

  return (
    <div
      className="police-en-route-banner"
      role="alert"
      data-engagement-id={row.id || undefined}
    >
      <div>
        <div className="police-en-route-banner-label">Law enforcement en route</div>
        <div className="police-en-route-banner-message">{message}</div>
      </div>
      <button
        type="button"
        className="police-en-route-banner-dismiss"
        aria-label="Dismiss en-route warning"
        onClick={() =>
          setDismissedKeys((prev) => {
            const next = new Set(prev);
            next.add(key);
            return next;
          })
        }
      >
        ×
      </button>
    </div>
  );
};

export default PoliceEnRouteBanner;
