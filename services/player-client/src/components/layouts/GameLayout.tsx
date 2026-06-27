import React, { useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { useGame } from '../../contexts/GameContext';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { useAutopilot } from '../../contexts/AutopilotContext';
// import { useTheme } from '../../themes/ThemeProvider'; // Available for future use
import PlayerVitalsHud from './PlayerVitalsHud';
import { MFDProvider, useMFD } from '../mfd/MFDContext';
import MFDScreen from '../mfd/MFDScreen';
import { SIDEBAR_A, SIDEBAR_B } from '../mfd/sidebarScreens';
import { ariaFeed } from '../mfd/ariaFeedStore';
import RouteRail from '../mfd/RouteRail';
import MedalToast from '../ranking/MedalToast';
import PriorityHailConsumer from '../comms/PriorityHailConsumer';
import './game-layout.css';
import '../../styles/themes/cockpit-animations.css';
import '../../styles/themes/cockpit-components.css';

interface GameLayoutProps {
  children: React.ReactNode;
}

/* MFD alert wiring — lives inside the MFDProvider subtree so it can badge
   softkeys; renders nothing. Each effect compares against the previous
   value held in a ref, so alerts fire on TRANSITIONS only (growth /
   became-paused / unread increase) and never on mount — a reload doesn't
   badge stale state. raiseAlert itself skips pages currently visible on
   either screen, so no visibility check is needed here. */
const MFDAlertWiring: React.FC = () => {
  const { raiseAlert } = useMFD();
  const { ariaMessages } = useWebSocket();
  const { status, course, pauseReason } = useAutopilot();
  const { unreadMessageCount } = useGame();

  const prevAriaCount = React.useRef(ariaMessages.length);
  React.useEffect(() => {
    if (ariaMessages.length > prevAriaCount.current) {
      raiseAlert('aria-event');
    }
    prevAriaCount.current = ariaMessages.length;
  }, [ariaMessages.length, raiseAlert]);

  // Autopilot transitions: badge AND narrate into the ARIA feed store.
  // Narration lives here (always mounted) rather than in AriaTerminalPage,
  // which unmounts whenever another MFD-B page is shown — transitions must
  // never be lost to softkey state (ADR-0072 §B3).
  const prevStatus = React.useRef(status);
  React.useEffect(() => {
    const prev = prevStatus.current;
    prevStatus.current = status;
    if (status === prev) return;

    if (status === 'engaged') {
      const totalHops = course?.hops?.length ?? 0;
      ariaFeed.appendNav(`Autopilot engaged — ${totalHops} hop${totalHops !== 1 ? 's' : ''}.`);
    }
    if (status === 'paused') {
      raiseAlert('autopilot-pause');
      ariaFeed.appendNav(`Autopilot paused — ${pauseReason ?? 'unknown reason'}.`);
    }
    if (status === 'arrived') {
      const targetId = course?.target_sector_id ?? '?';
      const totalTurns = course?.total_turns ?? '?';
      ariaFeed.appendNav(`Arrival: Sector ${targetId}. ${totalTurns} turns spent. Logged.`);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, raiseAlert]);

  const prevUnread = React.useRef(unreadMessageCount);
  React.useEffect(() => {
    if (unreadMessageCount > prevUnread.current) {
      raiseAlert('new-message');
    }
    prevUnread.current = unreadMessageCount;
  }, [unreadMessageCount, raiseAlert]);

  return null;
};

const GameLayout: React.FC<GameLayoutProps> = ({ children }) => {
  const { user } = useAuth();
  const { playerState, isLoading, isRefreshing, refreshPlayerState } = useGame();
  // const { currentTheme } = useTheme(); // Available for future use
  const [sidebarOpen, setSidebarOpen] = useState(true);

  // ── Scroll contract (Law 2) ──────────────────────────────────────────
  // On /game routes the DOCUMENT never scrolls: the shell locks html/body
  // overflow while mounted and restores the previous values on unmount
  // (login/landing pages keep their normal scroll behavior). Only monitor
  // interiors (.screen-hud-content) scroll.
  React.useEffect(() => {
    const html = document.documentElement;
    const body = document.body;
    const prevHtmlOverflow = html.style.overflow;
    const prevBodyOverflow = body.style.overflow;
    html.style.overflow = 'hidden';
    body.style.overflow = 'hidden';
    return () => {
      html.style.overflow = prevHtmlOverflow;
      body.style.overflow = prevBodyOverflow;
    };
  }, []);

  // ── Cockpit stability ────────────────────────────────────────────────
  // GameContext semantics after the isLoading split: `isLoading` is true
  // ONLY during initial hydration (playerState still null); background
  // refreshes flip the lightweight `isRefreshing` flag instead and never
  // unmount anything. The viewport children render unconditionally:
  //   • full loading overlay ONLY during the true initial load (we have
  //     never seen player state), rendered absolutely OVER the viewport;
  //   • background refreshes get at most a subtle SYNC indicator (keyed on
  //     isRefreshing) that appears only past ~300ms (no flicker).
  // State (not just a ref) so the SYNC-indicator effect below re-runs the
  // moment the latch flips. A pure ref flip during render does not retrigger
  // effects, leaving a dead window where a refresh that begins right as the
  // latch flips mid-load never starts the SYNC timer.
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);
  if (playerState && !hasLoadedOnce) {
    // Idempotent render-time latch: flips false→true exactly once,
    // safe under StrictMode double-render (setState during render with an
    // already-true value is a no-op).
    setHasLoadedOnce(true);
  }
  const isInitialLoad = isLoading && !hasLoadedOnce;

  const [showSyncIndicator, setShowSyncIndicator] = useState(false);
  React.useEffect(() => {
    if (isRefreshing && hasLoadedOnce) {
      const timer = window.setTimeout(() => setShowSyncIndicator(true), 300);
      return () => window.clearTimeout(timer);
    }
    setShowSyncIndicator(false);
    return undefined;
  }, [isRefreshing, hasLoadedOnce]);

  // Try to refresh player state on mount if we don't have it
  const hasAttemptedRefresh = React.useRef(false);
  React.useEffect(() => {
    if (user && !playerState && !isLoading && !hasAttemptedRefresh.current) {
      hasAttemptedRefresh.current = true;
      refreshPlayerState();
    }
    // Reset the ref if we get player state (allows retry on logout/login)
    if (playerState) {
      hasAttemptedRefresh.current = false;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, playerState, isLoading]); // Remove refreshPlayerState from deps to prevent loop
  
  const toggleSidebar = () => {
    setSidebarOpen(!sidebarOpen);
  };

  // ── Auto-collapse the sidebar on landing (WO 129-B) ──────────────────
  // Landing hands the full band to the planetary console, so the nav rail
  // is dead weight; auto-collapse it on the landing TRANSITION and restore
  // it on lift-off. Mirrors the windshield landed-min auto-behavior. We key
  // off the is_landed edge (tracked via a ref) — not every render — so the
  // manual ◀/▶ toggle is never fought while the player stays landed.
  const prevIsLandedRef = React.useRef<boolean>(!!playerState?.is_landed);
  React.useEffect(() => {
    const isLanded = !!playerState?.is_landed;
    if (isLanded !== prevIsLandedRef.current) {
      // close on the false→true (land) edge, open on the true→false (lift-off) edge
      setSidebarOpen(!isLanded);
      prevIsLandedRef.current = isLanded;
    }
  }, [playerState?.is_landed]);
  
  return (
    <div className="game-layout-wrapper">
      {/* Cockpit-wide realtime medal toast: consumes the medal_awarded WS event
          so a freshly-earned decoration pops on any /game route. */}
      <MedalToast />
      {/* Priority-driven hail surfaces (WO-B6): the in-game notification toast
          stack (normal/high messages + other WS toasts) and the urgent
          action-interrupting modal — per messaging.md "Priority levels". */}
      <PriorityHailConsumer />
      <div className="game-layout">
        {/* WO-INVERTED-L: .console-expand → docked/landed make the opaque
            console fill the lower area (right viewport column collapses);
            .console-collapsed → the edge-toggle hides the console for an
            unobstructed scene (rail-peek retired; logout lives in the HUD). */}
        <div
          className={`game-container${
            playerState?.is_docked || playerState?.is_landed ? ' console-expand' : ''
          }${sidebarOpen ? '' : ' console-collapsed'}`}
        >
          {/* Left console (NEON15): route rail on top, then two MFD
              screens splitting the remaining height. MFDProvider hosts
              page selection/alert state plus the alert wiring effects. */}
          <aside className={`game-sidebar hud-panel ${sidebarOpen ? 'open' : 'closed'}`}>
            <MFDProvider>
              <RouteRail />
              <MFDScreen config={SIDEBAR_A} />
              <MFDScreen config={SIDEBAR_B} />
              <MFDAlertWiring />
            </MFDProvider>
          </aside>

          <main className="game-content" aria-busy={isInitialLoad}>
            {/* Sidebar toggle, relocated from the deleted top header to the
                left edge of the viewport (the rail still owns the commander
                name). Keeps the original handler + ◀/▶ icon. */}
            <button
              className="cockpit-btn sidebar-toggle sidebar-edge-toggle"
              onClick={toggleSidebar}
              aria-label={sidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
              title={sidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
            >
              <span className="toggle-icon">{sidebarOpen ? '◀' : '▶'}</span>
            </button>
            {/* Children render UNCONDITIONALLY — never unmounted by a
                background refresh (see cockpit-stability note above).
                During the initial-load overlay the viewport is `inert`
                so its controls can't be tab-focused underneath. */}
            <div
              className="main-viewport"
              // `inert` isn't in the installed @types/react (18.x) surface yet,
              // but the DOM supports it and React passes unknown lowercase
              // attrs through. Spread it so hidden controls under the
              // initial-load overlay can't be tab-focused.
              {...(isInitialLoad ? { inert: '' } : {})}
            >
              <PlayerVitalsHud />
              {children}
            </div>
            {isInitialLoad && (
              <div className="viewport-loading-overlay">
                <div className="loading-spinner"></div>
                <p className="loading-text animate-typing">INITIALIZING SYSTEMS...</p>
              </div>
            )}
            {showSyncIndicator && !isInitialLoad && (
              <div className="sync-indicator" role="status" aria-live="polite" aria-label="Synchronizing">
                <span className="sync-indicator-dot"></span>
                <span className="sync-indicator-label">SYNC</span>
              </div>
            )}
          </main>
        </div>
      </div>
    </div>
  );
};

export default GameLayout;
