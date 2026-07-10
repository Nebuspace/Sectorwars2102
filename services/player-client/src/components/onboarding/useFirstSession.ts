import { useCallback, useEffect, useRef, useState } from 'react';
import { useGame } from '../../contexts/GameContext';
import { useWebSocket } from '../../contexts/WebSocketContext';

// WO-PUX-ONBOARD: first-session orientation. A brand-new player currently
// cold-drops from first-login straight into the full cockpit with zero
// guidance; this hook tracks three objectives (dock / trade / travel) using
// ONLY signals that already exist client-side -- no new server endpoints,
// no new WS event types.
//
//   • Dock   — playerState.is_docked (GameContext).
//   • Trade  — the shared WebSocketContext `notifications` queue already
//     gets a `{ title: 'Trade Successful' }` entry from TradingInterface's
//     own executeTrade() success handler (see components/trading/
//     TradingInterface.tsx) -- the only existing "a trade just completed"
//     signal client-side (an ordinary buy/sell is a plain REST call
//     resolved locally in that component, not a WS-pushed event, so there
//     is no dedicated signal/payload pair for it the way medal_awarded or
//     npc_combat_initiated get one). Observing that notification is
//     read-only and does not touch TradingInterface.tsx or GameContext.tsx.
//   • Travel — playerState.current_sector_id changing away from whatever it
//     was when this session armed.
//
// NO-CANON (flagged for design sign-off, per the WO): the objective triple
// itself and this exact detection method are an interpretation --
// player-journey.md names onboarding GOALS, not a scripted implementation.
//
// ARM: OutcomeDisplay.tsx sets a bare, non-player-keyed sessionStorage flag
// right before EVERY navigate('/game') call (both the happy path and the
// dropped-response recovery path -- see that file). "Armed" here means
// "a first-login completed THIS browser tab session" combined with
// whichever player is actually logged in once GameContext hydrates -- the
// flag itself doesn't need to know the player id at set-time.
//
// PERSISTENCE (also NO-CANON — localStorage vs. a server-side dismissal
// column is an interpretation, not a ruling): dismiss and per-objective
// progress are localStorage, keyed by player id, so a returning player in a
// DIFFERENT browser/device would see onboarding again. Dismiss and natural
// full-completion are both PERMANENT and share the same "retired" marker --
// once retired, the chip never renders again for that player, even after a
// remount/reload/relogin in the same browser.

const SESSION_ARM_KEY = 'sw:onboarding:armed';
const retiredKey = (playerId: string) => `sw:onboarding:retired:${playerId}`;
const progressKey = (playerId: string) => `sw:onboarding:progress:${playerId}`;

export interface FirstSessionProgress {
  dock: boolean;
  trade: boolean;
  travel: boolean;
}

const EMPTY_PROGRESS: FirstSessionProgress = { dock: false, trade: false, travel: false };

export interface UseFirstSessionResult {
  /** Should the chip currently be shown -- armed this session AND not (yet) retired. */
  visible: boolean;
  progress: FirstSessionProgress;
  allComplete: boolean;
  /** Permanent dismiss -- never shows again for this player. */
  dismiss: () => void;
}

function readProgress(playerId: string): FirstSessionProgress {
  try {
    const raw = localStorage.getItem(progressKey(playerId));
    if (!raw) return { ...EMPTY_PROGRESS };
    const parsed = JSON.parse(raw);
    return {
      dock: !!parsed.dock,
      trade: !!parsed.trade,
      travel: !!parsed.travel,
    };
  } catch {
    return { ...EMPTY_PROGRESS };
  }
}

export function useFirstSession(): UseFirstSessionResult {
  const { playerState } = useGame();
  const { notifications } = useWebSocket();

  const playerId = playerState?.id ?? null;

  const [armedThisSession, setArmedThisSession] = useState(false);
  const [retired, setRetired] = useState(false);
  const [progress, setProgress] = useState<FirstSessionProgress>(EMPTY_PROGRESS);

  const initializedForPlayer = useRef<string | null>(null);
  const startSectorRef = useRef<number | null>(null);

  // Initialize once we know WHICH player this is -- reads both storages
  // exactly once per playerId (never re-reads on every render).
  useEffect(() => {
    if (!playerId || initializedForPlayer.current === playerId) return;
    initializedForPlayer.current = playerId;

    const wasArmedThisSession = sessionStorage.getItem(SESSION_ARM_KEY) === '1';
    const isRetired = localStorage.getItem(retiredKey(playerId)) === '1';

    setArmedThisSession(wasArmedThisSession);
    setRetired(isRetired);
    setProgress(readProgress(playerId));
  }, [playerId]);

  // Capture the sector this session started in, once armed -- the "travel"
  // objective needs a baseline to detect a CHANGE against.
  useEffect(() => {
    if (armedThisSession && startSectorRef.current === null && playerState?.current_sector_id != null) {
      startSectorRef.current = playerState.current_sector_id;
    }
  }, [armedThisSession, playerState?.current_sector_id]);

  const tick = useCallback(
    (key: keyof FirstSessionProgress) => {
      if (!playerId) return;
      setProgress((prev) => {
        if (prev[key]) return prev; // already ticked -- idempotent
        const next = { ...prev, [key]: true };
        localStorage.setItem(progressKey(playerId), JSON.stringify(next));
        return next;
      });
    },
    [playerId]
  );

  // Dock objective.
  useEffect(() => {
    if (!armedThisSession || retired || progress.dock) return;
    if (playerState?.is_docked) tick('dock');
  }, [armedThisSession, retired, progress.dock, playerState?.is_docked, tick]);

  // Travel objective -- current sector diverged from the armed-session baseline.
  useEffect(() => {
    if (!armedThisSession || retired || progress.travel) return;
    if (
      startSectorRef.current !== null &&
      playerState?.current_sector_id != null &&
      playerState.current_sector_id !== startSectorRef.current
    ) {
      tick('travel');
    }
  }, [armedThisSession, retired, progress.travel, playerState?.current_sector_id, tick]);

  // Trade objective -- see the module doc-comment for why this is the
  // observed signal. Guarded by !progress.trade so this only ever fires
  // once regardless of how many renders see the same notification.
  useEffect(() => {
    if (!armedThisSession || retired || progress.trade) return;
    if (notifications.length > 0 && notifications[0].title === 'Trade Successful') {
      tick('trade');
    }
  }, [armedThisSession, retired, progress.trade, notifications, tick]);

  const allComplete = progress.dock && progress.trade && progress.travel;

  // Auto-retire on full completion -- permanent, same marker dismiss() uses.
  useEffect(() => {
    if (allComplete && playerId && !retired) {
      localStorage.setItem(retiredKey(playerId), '1');
      setRetired(true);
    }
  }, [allComplete, playerId, retired]);

  const dismiss = useCallback(() => {
    if (playerId) localStorage.setItem(retiredKey(playerId), '1');
    setRetired(true);
  }, [playerId]);

  return {
    visible: armedThisSession && !retired,
    progress,
    allComplete,
    dismiss,
  };
}

export default useFirstSession;
