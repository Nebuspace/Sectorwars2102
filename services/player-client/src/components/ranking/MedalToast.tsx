import React, { useEffect, useRef, useState } from 'react';
import { useWebSocket } from '../../contexts/WebSocketContext';
import './medal-toast.css';

/**
 * MedalToast — the cockpit toast consumer for the `medal_awarded` WS event.
 *
 * MedalShowcase only refreshes its grid on a signal; a pilot not on the ranking
 * page needs to SEE the decoration the moment it lands. This component watches
 * the WebSocketContext's `medalAwardedSignal` / `lastMedalAwarded` (bumped by the
 * `medal_awarded` frame from medal_service.award_medal) and pops a self-dismissing
 * gold showcase toast over the cockpit. It renders nothing until a medal arrives.
 *
 * Mounted once in GameLayout so it is alive on every /game route. Positioned
 * top-right, fixed, out of the primary-action band (SCROLL LAW): it overlays
 * chrome, never the controls a screen exists to provide, and auto-clears.
 */

// Icon map mirrors MedalShowcase's MEDAL_ICONS (the backend sends the legacy
// icon key in `medal_icon`); fall back to a generic medal glyph.
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

const VISIBLE_MS = 6000;

const MedalToast: React.FC = () => {
  const { medalAwardedSignal, lastMedalAwarded } = useWebSocket();
  const [visible, setVisible] = useState(false);
  const dismissTimer = useRef<number | null>(null);

  useEffect(() => {
    // signal 0 is the mount baseline (no medal yet) — only react to real bumps.
    if (medalAwardedSignal <= 0 || !lastMedalAwarded) return;

    setVisible(true);
    if (dismissTimer.current !== null) {
      window.clearTimeout(dismissTimer.current);
    }
    dismissTimer.current = window.setTimeout(() => setVisible(false), VISIBLE_MS);

    return () => {
      if (dismissTimer.current !== null) {
        window.clearTimeout(dismissTimer.current);
        dismissTimer.current = null;
      }
    };
  }, [medalAwardedSignal, lastMedalAwarded]);

  if (!visible || !lastMedalAwarded) return null;

  const icon = (lastMedalAwarded.medal_icon && MEDAL_ICONS[lastMedalAwarded.medal_icon]) || '🏅';

  return (
    <div className="medal-toast" role="status" aria-live="polite">
      <button
        className="medal-toast-close"
        onClick={() => setVisible(false)}
        aria-label="Dismiss medal notification"
      >
        ×
      </button>
      <div className="medal-toast-icon">{icon}</div>
      <div className="medal-toast-body">
        <div className="medal-toast-eyebrow">MEDAL EARNED</div>
        <div className="medal-toast-name">{lastMedalAwarded.medal_name || 'New Medal'}</div>
        {lastMedalAwarded.medal_description && (
          <div className="medal-toast-desc">{lastMedalAwarded.medal_description}</div>
        )}
        {lastMedalAwarded.medal_category && (
          <div className="medal-toast-cat">{lastMedalAwarded.medal_category}</div>
        )}
      </div>
    </div>
  );
};

export default MedalToast;
