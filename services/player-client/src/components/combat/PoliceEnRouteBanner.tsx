import React, { useEffect, useRef, useState } from 'react';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { useGame } from '../../contexts/GameContext';
import pendingEngagementApi, {
  PendingEngagementSummary,
} from '../../services/pendingEngagementApi';
import { formatPoliceEnRouteMessage } from './formatPoliceEnRouteMessage';
import './police-en-route-banner.css';

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
        }
      })
      .catch(() => {
        if (!cancelled) {
          setItems([]);
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
    return null;
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
