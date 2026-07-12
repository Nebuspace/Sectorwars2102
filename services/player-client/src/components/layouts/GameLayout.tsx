import React, { useLayoutEffect, useRef, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { useGame } from '../../contexts/GameContext';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { useAutopilot } from '../../contexts/AutopilotContext';
// import { useTheme } from '../../themes/ThemeProvider'; // Available for future use
import StatusBar from './StatusBar';
import Teleprinter from '../aria/Teleprinter';
import Annunciator from '../hud/Annunciator';
import { MFDProvider, useMFD } from '../mfd/MFDContext';
import MFDScreen from '../mfd/MFDScreen';
import { SIDEBAR_A, SIDEBAR_B } from '../mfd/sidebarScreens';
import { ariaFeed } from '../mfd/ariaFeedStore';
import RouteRail from '../mfd/RouteRail';
import MedalToast from '../ranking/MedalToast';
import PriorityHailConsumer from '../comms/PriorityHailConsumer';
import WelcomeBackToast from '../auth/WelcomeBackToast';
import NpcCombatBanner from '../combat/NpcCombatBanner';
import FirstSessionObjectives from '../onboarding/FirstSessionObjectives';
import { useFirstSession } from '../onboarding/useFirstSession';
import { ShellPresenceContext, useShellPresent } from './ShellContext';
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
  const { unreadMessageCount, playerState, currentSector, stationsInSector } = useGame();

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

  // WO-PUX-ONBOARD: first-session orientation narration, into the SAME
  // ariaFeed.appendNav channel the autopilot transitions above use. One
  // effect, ref-diffed exactly like those (fires on TRANSITIONS only, never
  // replays on a later re-render/remount). Lines are deterministic, built
  // from real state (turn count / current sector name / port presence) --
  // NO-CANON copy, see useFirstSession's doc-comment.
  const { visible: firstSessionArmed, progress: firstSessionProgress, allComplete: firstSessionComplete } =
    useFirstSession();
  const prevFirstSession = React.useRef({ armed: false, dock: false, trade: false, travel: false, complete: false });
  React.useEffect(() => {
    const prev = prevFirstSession.current;
    const turns = playerState?.turns ?? 0;
    const sectorName = currentSector?.name || `Sector ${playerState?.current_sector_id ?? '?'}`;

    if (firstSessionArmed && !prev.armed) {
      const hasPort = stationsInSector.length > 0;
      ariaFeed.appendNav(
        `Orientation started, Commander. ${turns} turn${turns === 1 ? '' : 's'} banked in ${sectorName}` +
        `${hasPort ? ' — a station is right here.' : '.'} Three objectives ahead: dock, trade, travel.`
      );
    }
    if (firstSessionProgress.dock && !prev.dock) {
      ariaFeed.appendNav('Docking confirmed. Objective cleared — dock at a station.');
    }
    if (firstSessionProgress.trade && !prev.trade) {
      ariaFeed.appendNav('First trade logged. Objective cleared — make a trade.');
    }
    if (firstSessionProgress.travel && !prev.travel) {
      ariaFeed.appendNav(`Arrived in ${sectorName}. Objective cleared — travel to a new sector.`);
    }
    if (firstSessionComplete && !prev.complete) {
      ariaFeed.appendNav(
        `Orientation complete, Commander — all three objectives cleared in ${turns} turn${turns === 1 ? '' : 's'}. ` +
        `You're clear for open operations.`
      );
    }

    prevFirstSession.current = {
      armed: firstSessionArmed,
      dock: firstSessionProgress.dock,
      trade: firstSessionProgress.trade,
      travel: firstSessionProgress.travel,
      complete: firstSessionComplete,
    };
  }, [firstSessionArmed, firstSessionProgress, firstSessionComplete, playerState?.turns, playerState?.current_sector_id, currentSector?.name, stationsInSector.length]);

  return null;
};

const GameLayout: React.FC<GameLayoutProps> = ({ children }) => {
  // ── Persistent-shell passthrough (WO-UI0-PERSISTENT-SHELL lane B) ────────
  // If an ancestor shell already provides the cockpit chrome (shellPresent),
  // a nested <GameLayout> call becomes a no-op — mirrors EmbeddedContext's
  // HangarShell/ColonialShell pattern so two shells never nest. Dormant:
  // shellPresent stays false everywhere until Lane A lands the persistent
  // shell provider, so this early return never fires yet.
  const shellPresent = useShellPresent();
  if (shellPresent) return <>{children}</>;

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

  // ── Deck reflow — reserve the real teleprinter height (WO-UI1-DECK-REFLOW) ─
  // .game-content/.game-sidebar are absolute overlays bounded by
  // `calc(var(--statusbar-h) + var(--teleprinter-h))` (game-layout.css) so
  // the deck/sidebar stop above the statusbar+teleprinter grid rows instead
  // of extending underneath them. --statusbar-h is a static 56px (StatusBar
  // never changes size) but Teleprinter genuinely does — minimized strip vs
  // full body, vh-clamped log, input growing on focus — so it's measured off
  // the live DOM node rather than reserved at a fixed worst-case (which would
  // either overlap or permanently waste deck space whenever minimized).
  // useLayoutEffect (not useEffect) so the first real value lands before the
  // browser paints — no one-frame flash of the CSS fallback. ResizeObserver
  // is undefined in this repo's jsdom test env (confirmed: jsdom 29 ships no
  // implementation), so component tests skip live measurement and just keep
  // the CSS default — harmless, since jsdom never asserts real geometry.
  const gameContainerRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const container = gameContainerRef.current;
    if (!container) return undefined;
    const teleprinterEl = container.querySelector<HTMLElement>('.teleprinter');
    if (!teleprinterEl) return undefined;

    const applyHeight = () => {
      container.style.setProperty('--teleprinter-h', `${teleprinterEl.offsetHeight}px`);
    };
    applyHeight();

    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(applyHeight);
    observer.observe(teleprinterEl);
    return () => observer.disconnect();
  }, []);

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

  // ── Windshield minimize / expand (id=151) ────────────────────────────
  // Docked/landed hand the lower area to the station/colony console, so AUTO-
  // minimize the windshield band on the dock/land EDGE — shrinking --band-h at
  // the container so the helm + sidebar + deck rise and the console gets the
  // reclaimed vertical space (SCROLL LAW) — and restore it on the undock/
  // lift-off edge. A manual toggle (button in the band) lets the player expand
  // the scene back at will. Keyed off the edge (ref), not every render, so the
  // manual toggle isn't fought while the player stays grounded. (Restores the
  // retired green-bar minimize, recomposed for the inverted-L.)
  const grounded = !!(playerState?.is_docked || playerState?.is_landed);
  const [windshieldMin, setWindshieldMin] = useState(false);
  const prevGroundedRef = React.useRef<boolean>(grounded);
  React.useEffect(() => {
    const g = !!(playerState?.is_docked || playerState?.is_landed);
    if (g !== prevGroundedRef.current) {
      setWindshieldMin(g); // minimize on dock/land, restore on undock/lift-off
      prevGroundedRef.current = g;
    }
  }, [playerState?.is_docked, playerState?.is_landed]);
  const toggleWindshield = () => setWindshieldMin((m) => !m);

  // ── Mode classes (WO-UI0-PERSISTENT-SHELL lane B, ADDITIVE per ruling D3) ─
  // Layered alongside (never replacing) the legacy console-expand/windshield-
  // min/landed-expanded classes above, which still drive the --band-h/
  // --sidebar-w/--deck-h var math — this class carries no styling of its own
  // yet. Landed wins over docked, matching today's landed-expanded precedence.
  const mode = playerState?.is_landed ? 'mode-surface' : playerState?.is_docked ? 'mode-station' : 'mode-flight';

  return (
    <ShellPresenceContext.Provider value={true}>
    <div className="game-layout-wrapper">
      {/* Cockpit-wide realtime medal toast: consumes the medal_awarded WS event
          so a freshly-earned decoration pops on any /game route. */}
      <MedalToast />
      {/* Priority-driven hail surfaces (WO-B6): the in-game notification toast
          stack (normal/high messages + other WS toasts) and the urgent
          action-interrupting modal — per messaging.md "Priority levels". */}
      <PriorityHailConsumer />
      <WelcomeBackToast />
      {/* NPC-initiated combat alert (WO-CMB-NPC-INITIATED-1 lane D): the
          npc_combat_initiated WS event's defender-side banner. */}
      <NpcCombatBanner />
      {/* First-session orientation chip (WO-PUX-ONBOARD) -- renders nothing
          unless this tab just landed here from first-login completion. */}
      <FirstSessionObjectives />
      <div className="game-layout">
        {/* WO-INVERTED-L: .console-expand → docked/landed make the opaque
            console fill the lower area (right viewport column collapses);
            .console-collapsed → the edge-toggle hides the console for an
            unobstructed scene (rail-peek retired; logout lives in the HUD). */}
        <div
          ref={gameContainerRef}
          className={`game-container ${mode}${
            playerState?.is_docked || playerState?.is_landed ? ' console-expand' : ''
          }${sidebarOpen ? '' : ' console-collapsed'}${
            windshieldMin && grounded ? ' windshield-min' : ''
          }${
            playerState?.is_landed && !windshieldMin ? ' landed-expanded' : ''
          }`}
        >
          {/* Annunciator (WO-UI1-ANNUNCIATOR stitch) — mounted inside a
              dedicated, non-visual `.windshield-hud-anchor` (game-layout.css)
              rather than directly inside `.game-content`: that layer spans
              the FULL container height (out-of-grid-flow, for the
              inverted-L scene), which would let Annunciator's own
              `position:absolute; inset:0` overlay technically extend behind
              the statusbar/teleprinter rows too. The anchor is a real,
              non-absolute grid child assigned to the already-reserved
              `windshield` grid-area (game-layout.css:100-104), scoping
              Annunciator to just that row — "on the glass, never over the
              status bar," structurally, independent of z-index. */}
          <div className="windshield-hud-anchor">
            <Annunciator />
          </div>

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
              inert={isInitialLoad}
            >
              {/* Windshield minimize/expand (id=151 + id=151b) — only while
                  docked/landed. Both states show a CENTERED, labeled button.
                  MINIMIZED: "Expand Viewport" near top of the thin 60px band.
                  EXPANDED: "Minimize Viewport" centered-bottom of the scene
                  band (uses --band-h via .windshield-minimize). */}
              {grounded && windshieldMin && (
                <button
                  type="button"
                  className="windshield-expand"
                  onClick={toggleWindshield}
                  aria-label="Expand viewport"
                  title="Expand the viewport"
                >
                  ⤢ Expand Viewport
                </button>
              )}
              {grounded && !windshieldMin && (
                <button
                  type="button"
                  className="windshield-minimize"
                  onClick={toggleWindshield}
                  aria-label="Minimize viewport"
                  title="Minimize viewport — give the console more room"
                >
                  ▾ MINIMIZE VIEWPORT
                </button>
              )}
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

          {/* StatusBar (WO-UI0-STATUSBAR) — a DIRECT, non-absolute child of
              .game-container so CSS Grid actually places it into the
              reserved `statusbar` grid-area (game-layout.css:96-103); a
              descendant nested inside .main-viewport/.game-content (both
              position:absolute, out of grid flow) would NOT land there.
              Supersedes PlayerVitalsHud (unmounted above; file left in
              place per WO, not deleted). */}
          <StatusBar />

          {/* Teleprinter (WO-UI1-TELEPRINTER stitch) — same pattern as
              StatusBar: a DIRECT, non-absolute child of .game-container so
              CSS Grid places it into the reserved `teleprinter` grid-area
              (game-layout.css:100-104); teleprinter.css already carries
              `grid-area: teleprinter` on the component's own root, so no
              wrapper is needed here (unlike Annunciator, whose root is
              itself `position:absolute` and can't participate in grid
              placement on its own). */}
          <Teleprinter />
        </div>
      </div>
    </div>
    </ShellPresenceContext.Provider>
  );
};

export default GameLayout;
