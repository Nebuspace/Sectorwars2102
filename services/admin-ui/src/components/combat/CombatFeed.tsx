import React, { useEffect, useRef } from 'react';

// Shape matches the backend CombatFeedItem returned by GET /api/v1/admin/combat/live
// (services/gameserver/.../admin_combat.py). The previous interface declared a flat
// shape while the render read a nested `event.result.*` / `event.attacker.ship` shape
// that does not exist on the payload — so any non-empty feed threw
// `undefined.winner` / `undefined.toLocaleString()` at render. This reconciles the
// component to the real payload and accesses the dict fields defensively.
interface CombatParticipant {
  id: string;
  type: string;
  name: string;
  level?: number | null;
  team_id?: string | null;
  owner_id?: string | null;
}

interface CombatEvent {
  id: string;
  combat_type: string;
  status: string;
  started_at: string;
  ended_at?: string | null;
  duration_seconds: number;
  current_round: number;
  sector?: { name?: string; id?: string; [key: string]: any } | null;
  attacker: CombatParticipant;
  defender: CombatParticipant;
  combat_stats?: Record<string, any> | null;
  victor_id?: string | null;
  is_active: boolean;
  needs_intervention: boolean;
}

interface CombatFeedProps {
  // Loosely typed at the boundary: CombatOverview keeps its own (historically
  // flat) CombatEvent type; the real runtime payload is the nested CombatFeedItem
  // modelled above, which this component maps over with that annotation.
  events: any[];
  onDisputeClick?: (eventId: string) => void;
  onInterventionClick?: (eventId: string) => void;
}

export const CombatFeed: React.FC<CombatFeedProps> = ({
  events,
  onDisputeClick,
  onInterventionClick
}) => {
  const feedRef = useRef<HTMLDivElement>(null);
  const isAutoScrolling = useRef(true);

  useEffect(() => {
    // Auto-scroll to bottom when new events arrive
    if (feedRef.current && isAutoScrolling.current) {
      feedRef.current.scrollTop = feedRef.current.scrollHeight;
    }
  }, [events]);

  const handleScroll = () => {
    if (feedRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = feedRef.current;
      // Check if user has scrolled away from bottom
      isAutoScrolling.current = scrollHeight - scrollTop - clientHeight < 50;
    }
  };

  const formatDuration = (seconds: number): string => {
    const total = Math.max(0, Math.floor(seconds || 0));
    const minutes = Math.floor(total / 60);
    const secs = total % 60;
    return `${minutes}m ${secs}s`;
  };

  const formatTimestamp = (timestamp?: string): string => {
    if (!timestamp) return '—';
    const date = new Date(timestamp);
    return isNaN(date.getTime()) ? '—' : date.toLocaleTimeString();
  };

  // The backend reports a victor_id (or null); resolve it to attacker/defender/draw.
  const resolveWinner = (event: CombatEvent): 'attacker' | 'defender' | 'draw' => {
    if (!event.victor_id) return 'draw';
    if (event.victor_id === event.attacker?.id) return 'attacker';
    if (event.victor_id === event.defender?.id) return 'defender';
    return 'draw';
  };

  const getResultColor = (winner: string): string => {
    switch (winner) {
      case 'attacker': return 'combat-winner-attacker';
      case 'defender': return 'combat-winner-defender';
      default: return 'combat-winner-draw';
    }
  };

  const participantLabel = (p?: CombatParticipant): string => {
    if (!p) return 'Unknown';
    const lvl = p.level != null ? ` L${p.level}` : '';
    return `${p.name ?? 'Unknown'}${lvl}`;
  };

  return (
    <div className="combat-feed">
      <div className="combat-feed-header">
        <h3>Live Combat Feed</h3>
        <span className="combat-count">{events.length} battles</span>
      </div>

      <div
        className="combat-feed-scroll"
        ref={feedRef}
        onScroll={handleScroll}
      >
        {events.length === 0 && (
          <div className="combat-feed-empty" style={{ padding: '16px', color: 'var(--text-tertiary)' }}>
            No active combat.
          </div>
        )}
        {events.map((event: CombatEvent) => {
          const winner = resolveWinner(event);
          return (
            <div key={event.id} className="combat-event">
              <div className="combat-event-header">
                <span className="combat-time">{formatTimestamp(event.started_at)}</span>
                <span className={`combat-result ${getResultColor(winner)}`}>
                  {event.is_active
                    ? 'IN PROGRESS'
                    : winner === 'draw' ? 'DRAW' : `${winner.toUpperCase()} WINS`}
                </span>
              </div>

              <div className="combat-participants">
                <div className="combat-attacker">
                  <span className="participant-name">{participantLabel(event.attacker)}</span>
                  <span className="participant-ship">({event.attacker?.type})</span>
                </div>

                <div className="combat-vs">VS</div>

                <div className="combat-defender">
                  <span className="participant-name">{participantLabel(event.defender)}</span>
                  <span className="participant-ship">({event.defender?.type})</span>
                </div>
              </div>

              <div className="combat-details">
                <div className="combat-location">
                  <i className="icon-location"></i>
                  {event.sector?.name ?? event.sector?.id ?? '—'}
                </div>

                <div className="combat-stats">
                  <span className="stat-item">{event.combat_type} · {event.status}</span>
                  <span className="stat-item">Round {event.current_round}</span>
                  <span className="stat-item">
                    <i className="icon-duration"></i>
                    Duration: {formatDuration(event.duration_seconds)}
                  </span>
                </div>
              </div>

              <div className="combat-actions">
                {event.needs_intervention && (
                  <span className="dispute-badge">NEEDS INTERVENTION</span>
                )}

                <button
                  className="btn-action btn-dispute"
                  onClick={() => onDisputeClick?.(event.id)}
                  title="View dispute details"
                >
                  <i className="icon-dispute"></i>
                  Dispute
                </button>

                <button
                  className="btn-action btn-intervene"
                  onClick={() => onInterventionClick?.(event.id)}
                  title="Admin intervention"
                >
                  <i className="icon-intervene"></i>
                  Intervene
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
