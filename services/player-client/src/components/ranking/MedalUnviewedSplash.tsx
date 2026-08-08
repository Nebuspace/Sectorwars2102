import React, { useEffect, useRef, useState } from 'react';
import { medalsAPI } from '../../services/api';
import './medal-toast.css';

/**
 * MedalUnviewedSplash — login-splash consumer for GET /api/v1/medals/unviewed.
 *
 * WO-WIRE-MEDALS-UNVIEWED-SPLASH / medals.md cross-session queue: awards earned
 * offline are persisted server-side; this component polls once on GameLayout
 * mount. Viewing clears the queue (server clear-on-view). Empty / error → render
 * nothing. Mounted beside MedalToast (realtime WS path).
 */

const VISIBLE_MS = 8000;

const MedalUnviewedSplash: React.FC = () => {
  const [ids, setIds] = useState<string[]>([]);
  const [visible, setVisible] = useState(false);
  const fetched = useRef(false);
  const dismissTimer = useRef<number | null>(null);

  useEffect(() => {
    if (fetched.current) return;
    fetched.current = true;
    let cancelled = false;

    (async () => {
      try {
        const data = await medalsAPI.getUnviewed();
        if (cancelled) return;
        const list = Array.isArray(data?.unviewed) ? data.unviewed.map(String) : [];
        if (list.length === 0) return;
        setIds(list);
        setVisible(true);
        dismissTimer.current = window.setTimeout(() => setVisible(false), VISIBLE_MS);
      } catch {
        // Network / auth blip — no splash; queue remains until next successful poll.
      }
    })();

    return () => {
      cancelled = true;
      if (dismissTimer.current !== null) {
        window.clearTimeout(dismissTimer.current);
        dismissTimer.current = null;
      }
    };
  }, []);

  if (!visible || ids.length === 0) return null;

  const count = ids.length;
  const title = count === 1 ? 'MEDAL EARNED WHILE AWAY' : `${count} MEDALS EARNED WHILE AWAY`;

  return (
    <div
      className="medal-toast medal-toast--unviewed"
      role="status"
      aria-live="polite"
      data-testid="medal-unviewed-splash"
    >
      <button
        className="medal-toast-close"
        onClick={() => setVisible(false)}
        aria-label="Dismiss offline medal notification"
      >
        ×
      </button>
      <div className="medal-toast-icon" aria-hidden>
        🏅
      </div>
      <div className="medal-toast-body">
        <div className="medal-toast-eyebrow">{title}</div>
        <ul className="medal-toast-unviewed-list">
          {ids.map((id) => (
            <li key={id} className="medal-toast-name">
              {id}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
};

export default MedalUnviewedSplash;
