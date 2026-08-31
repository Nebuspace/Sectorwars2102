import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { medalsAPI } from '../../services/api';
import { useWebSocket } from '../../contexts/WebSocketContext';
import './ranking.css';

interface Medal {
  key: string;
  name: string;
  category: string;
  description: string;
  icon: string;
  awarded_at?: string;
  value_at_award?: number;
  trigger_type?: string;
  threshold?: number;
}

interface MedalData {
  earned: Medal[];
  available: Medal[];
  pinned_medal_id?: string | null;
}

const CATEGORY_ICONS: Record<string, string> = {
  Combat: '⚔️',
  Economic: '💰',
  Exploration: '🌌',
  Diplomatic: '🤝',
  Special: '✨',
};

const MEDAL_ICONS: Record<string, string> = {
  star_bronze: '🥉',
  star_silver: '🥈',
  cross_quantum: '✝️',
  medal_trade: '🏅',
  crown_merchant: '👑',
  badge_explorer: '🧭',
  award_genesis: '🌍',
  star_ambassador: '⭐',
  favor_aria: '💜',
  cat_orange: '🐈',
  blood_first: '🩸',
  flag_colony: '🚩',
  commander_fleet: '🎖️',
};

const CATEGORIES = ['All', 'Combat', 'Economic', 'Exploration', 'Diplomatic', 'Special'];

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

/** True when err looks like gameserver detail (not bare API Error: N / TypeError noise). */
function hasMedalShowcaseServerDetail(err: unknown, message: string | undefined): boolean {
  // Network collapse (fetch TypeError) is not gameserver copy — use the caller fallback.
  if (err instanceof TypeError) return false;
  return (
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim())
  );
}

/** apiRequest throws Error with `.status`; surface gameserver detail on initial medal load. */
export function formatMedalShowcaseLoadError(err: unknown): string {
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  const hasServerDetail = hasMedalShowcaseServerDetail(err, message);

  if (status === 404) {
    if (hasServerDetail) return message!;
    return 'Failed to load medals';
  }

  if (hasServerDetail) return message!;
  return 'Failed to load medals';
}

const MedalShowcase: React.FC = () => {
  const [medalData, setMedalData] = useState<MedalData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeCategory, setActiveCategory] = useState('All');
  const [hoveredMedal, setHoveredMedal] = useState<Medal | null>(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });
  const [pinError, setPinError] = useState<string | null>(null);
  const [pinningKey, setPinningKey] = useState<string | null>(null);

  // Realtime: the WS context bumps medalAwardedSignal whenever a medal_awarded
  // frame arrives (medal_service.award_medal → send_medal_awarded). MedalShowcase
  // is otherwise pull-only, so it watches that counter to re-fetch its grid live.
  const { medalAwardedSignal } = useWebSocket();

  // showInitialSpinner: only the FIRST load shows the full-panel spinner. A
  // realtime re-fetch refreshes in place so the existing grid never blanks out.
  const fetchMedals = useCallback(async (showInitialSpinner: boolean) => {
    try {
      if (showInitialSpinner) setLoading(true);
      const data = await medalsAPI.getMe();
      setMedalData(data);
      setError(null);
    } catch (err: any) {
      // Only surface the error overlay on the initial load; a failed live
      // re-fetch keeps the last-known grid rather than wiping it.
      if (showInitialSpinner) setError(formatMedalShowcaseLoadError(err));
    } finally {
      if (showInitialSpinner) setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchMedals(true);
  }, [fetchMedals]);

  // Re-fetch when a medal is awarded in realtime (skip the mount tick: the
  // initial fetch above already covers signal 0).
  useEffect(() => {
    if (medalAwardedSignal > 0) {
      fetchMedals(false);
    }
  }, [medalAwardedSignal, fetchMedals]);

  const filteredMedals = useMemo(() => {
    if (!medalData) return { earned: [], available: [] };
    const filterFn = (m: Medal) =>
      activeCategory === 'All' || m.category === activeCategory;
    // Defensive: MedalData's shape is enforced by the TS type, not at
    // runtime -- a malformed/incomplete 200 must not crash the showcase.
    return {
      earned: (medalData.earned ?? []).filter(filterFn),
      available: (medalData.available ?? []).filter(filterFn),
    };
  }, [medalData, activeCategory]);

  const handleMouseEnter = (medal: Medal, e: React.MouseEvent) => {
    setHoveredMedal(medal);
    setTooltipPos({ x: e.clientX + 12, y: e.clientY - 10 });
  };

  const handlePinToggle = async (medal: Medal) => {
    if (!medalData) return;
    const isPinned = medalData.pinned_medal_id === medal.key;
    const nextPin = isPinned ? null : medal.key;
    setPinError(null);
    setPinningKey(medal.key);
    try {
      const result = await medalsAPI.pinMe(nextPin);
      setMedalData((prev) =>
        prev ? { ...prev, pinned_medal_id: result.pinned_medal_id } : prev,
      );
    } catch (err: any) {
      setPinError(err.message || 'Failed to update pinned medal');
    } finally {
      setPinningKey(null);
    }
  };

  if (loading) {
    return (
      <div className="medal-showcase medal-loading">
        <div className="rank-spinner" />
        <span>Loading medals...</span>
      </div>
    );
  }

  if (error || !medalData) {
    return (
      <div className="medal-showcase medal-error">
        <span>{error || 'Medals unavailable'}</span>
      </div>
    );
  }

  return (
    <div className="medal-showcase">
      <div className="medal-header">
        <h3>Medals</h3>
        <span className="medal-count">
          {(medalData.earned ?? []).length} / {(medalData.earned ?? []).length + (medalData.available ?? []).length}
        </span>
      </div>

      <div className="medal-categories">
        {CATEGORIES.map((cat) => (
          <button
            key={cat}
            className={`medal-cat-btn ${activeCategory === cat ? 'active' : ''}`}
            onClick={() => setActiveCategory(cat)}
          >
            {cat !== 'All' && CATEGORY_ICONS[cat]} {cat}
          </button>
        ))}
      </div>

      {pinError && (
        <div className="medal-pin-error" role="alert">
          {pinError}
        </div>
      )}

      <div className="medal-grid">
        {filteredMedals.earned.map((medal) => {
          const isPinned = medalData.pinned_medal_id === medal.key;
          const isPinning = pinningKey === medal.key;
          return (
          <div
            key={medal.key}
            className={`medal-card earned${isPinned ? ' pinned' : ''}`}
            onMouseEnter={(e) => handleMouseEnter(medal, e)}
            onMouseLeave={() => setHoveredMedal(null)}
          >
            <span className="medal-icon">
              {MEDAL_ICONS[medal.icon] || '🏅'}
            </span>
            <span className="medal-name">{medal.name}</span>
            {medal.awarded_at && (
              <span className="medal-date">
                {new Date(medal.awarded_at).toLocaleDateString()}
              </span>
            )}
            <button
              type="button"
              className={`medal-pin-btn${isPinned ? ' active' : ''}`}
              aria-label={isPinned ? `Unpin ${medal.name}` : `Pin ${medal.name}`}
              aria-pressed={isPinned}
              disabled={isPinning}
              onClick={(e) => {
                e.stopPropagation();
                void handlePinToggle(medal);
              }}
            >
              {isPinning ? '…' : isPinned ? '📌' : '📍'}
            </button>
          </div>
          );
        })}
        {filteredMedals.available.map((medal) => (
          <div
            key={medal.key}
            className="medal-card unearned"
            onMouseEnter={(e) => handleMouseEnter(medal, e)}
            onMouseLeave={() => setHoveredMedal(null)}
          >
            <span className="medal-icon">
              {MEDAL_ICONS[medal.icon] || '🏅'}
            </span>
            <span className="medal-name">{medal.name}</span>
          </div>
        ))}
      </div>

      {hoveredMedal && (
        <div
          className="medal-tooltip"
          style={{ top: tooltipPos.y, left: tooltipPos.x }}
        >
          <h4>{hoveredMedal.name}</h4>
          <p>{hoveredMedal.description}</p>
          <div className="tooltip-category">
            {CATEGORY_ICONS[hoveredMedal.category]} {hoveredMedal.category}
          </div>
        </div>
      )}
    </div>
  );
};

export default MedalShowcase;
