import React, { useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
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
import { SIDEBAR_A, SIDEBAR_B, SIDEBAR_A_FOLDED } from '../mfd/sidebarScreens';
import { ariaFeed } from '../mfd/ariaFeedStore';
import MedalToast from '../ranking/MedalToast';
import PriorityHailConsumer from '../comms/PriorityHailConsumer';
import WelcomeBackToast from '../auth/WelcomeBackToast';
import NpcCombatBanner from '../combat/NpcCombatBanner';
import FirstSessionObjectives from '../onboarding/FirstSessionObjectives';
import { useFirstSession } from '../onboarding/useFirstSession';
import { ShellPresenceContext, useShellPresent, ShellSlotsContext } from './ShellContext';
import './game-layout.css';
import './cockpit-shell.css';
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
  const { status, course, pauseReason, lastPlot } = useAutopilot();
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

  // WO-UI1-CHROME-COMPLETE: plot-result narration (ADR-0072 §B3), ported
  // from the retired AriaTerminalPage.tsx's pendingPlotRef/lastPlot effect
  // but WIDENED to fire on every lastPlot transition rather than only ones
  // the teleprinter itself issued — `plotCourse` is a shared AutopilotContext
  // call with multiple callers (Teleprinter's "set course to N" grammar,
  // the NAV deck's COURSE tab, GalaxyMap.tsx), and this effect lives in the
  // ALWAYS-mounted MFDAlertWiring (same reasoning as the status-transition
  // effect above it: transitions must never be lost to softkey/page state).
  // ref-diffed so it fires on TRANSITIONS only, never on mount/remount.
  const prevLastPlotRef = React.useRef(lastPlot);
  React.useEffect(() => {
    const prev = prevLastPlotRef.current;
    prevLastPlotRef.current = lastPlot;
    if (lastPlot === prev || lastPlot === null || lastPlot === undefined) return;

    if (lastPlot.reachable) {
      const hopCount = lastPlot.hops?.length ?? 0;
      ariaFeed.appendNav(
        `Course laid in for Sector ${lastPlot.target_sector_id} — ${hopCount} charted hop${hopCount !== 1 ? 's' : ''}, ${lastPlot.total_turns} turns. Say engage, or use the helm.`
      );
    } else if (lastPlot.reachable === false) {
      if (lastPlot.nearest_known) {
        ariaFeed.appendNav(
          `Sector ${lastPlot.target_sector_id} is beyond my charts. Nearest charted approach is Sector ${lastPlot.nearest_known.sector_id}. Fly the frontier and I will learn the route.`
        );
      } else {
        ariaFeed.appendNav('No such sector on any chart I can read.');
      }
    }
  }, [lastPlot]);

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

  // ── Teleprinter display toggles (WO-UI1-CHROME-COMPLETE; WO-UI-MAX-
  // BATCH-1 REVISE — Max #22-24 retracted the shipped single 3-state cycle
  // back to the artifact's own TWO INDEPENDENT BINARY TOGGLES) ───────────
  // Owned here (not inside Teleprinter), same rationale as before: PANEL
  // still drives which MFD-A config the sidebar renders (the MFD-B→MFD-A
  // fold, below) — a decision GameLayout must see. `transcriptOpen` (LOG
  // open/closed) has no cross-component consumer, but is kept alongside
  // `teleprinterBodyPanel` for symmetry (both are "the teleprinter mode
  // state" this WO's file lane assigns to GameLayout, not Teleprinter) and
  // so a future consumer never needs to hunt for it in two places.
  const [teleprinterBodyPanel, setTeleprinterBodyPanel] = useState(false);
  const [teleprinterTranscriptOpen, setTeleprinterTranscriptOpen] = useState(false);

  // ── Redirect focus management (WCAG 2.4.3, Pixel a11y gate) ───────────
  // GameShellRoute/GameLayout mount ONCE and persist across every /game/*
  // navigation (see GameShellRoute's doc-comment) -- a legacy /game/<path>
  // URL (GameRouteRedirects.tsx) lands here via a client-side <Navigate>,
  // which drops focus onto <body> with nothing announced. On every
  // location.pathname TRANSITION within this persistent shell, move focus
  // to the cockpit's own <main> landmark below. Ref-tracked previous value
  // mirrors LocationDropdown's wasOpenRef idiom so it never steals focus on
  // the FIRST mount (fresh login landing on /game) -- only on a genuine
  // route change while the shell is already up.
  const location = useLocation();
  const mainLandmarkRef = useRef<HTMLElement>(null);
  const prevPathnameRef = useRef(location.pathname);
  React.useEffect(() => {
    if (location.pathname !== prevPathnameRef.current) {
      mainLandmarkRef.current?.focus();
    }
    prevPathnameRef.current = location.pathname;
  }, [location.pathname]);

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

  // ── Shell slots (WO-UI0-SHELL-TRANSPLANT) — `.band`/`.deck` portal targets ─
  // REVISION (adversarial-review catch, Mack): a callback-ref-driven
  // `useState<HTMLDivElement | null>(null)` makes `bandEl` null on GameLayout's
  // OWN first render, non-null from the second. A consumer that does
  // `bandEl ? createPortal(node, bandEl) : node` at ONE JSX position sees the
  // element TYPE at that position flip (a plain host element -> a
  // ReactPortal) across that transition — React does not preserve identity
  // across a type change, so it unmounts the whole inline subtree and mounts
  // a fresh one through the portal. Empirically reproduced in plain React
  // (mount count 1->2, unmount 0->1, no StrictMode needed) — for
  // GameDashboard's `.cockpit-console` this meant NavigationMap/
  // TacticalMonitor (both carry real mount-effects) mounted inline once,
  // then got torn down and rebuilt through the portal, on EVERY session
  // start.
  // FIX: create the target nodes EAGERLY via a `useState` LAZY INITIALIZER
  // (`document.createElement`), which runs synchronously during THIS
  // component's very first render, before any descendant (including
  // `{children}`) has rendered at all. `bandEl`/`deckEl` are therefore
  // non-null from the FIRST render everywhere down the tree — a consumer's
  // `bandEl ? createPortal(...) : ...` ternary sees the SAME branch (the
  // portal one) on every render, in production, with the type at that JSX
  // position never changing -- no remount. The nodes aren't yet attached to
  // the visible DOM at creation time; a `useLayoutEffect` below appends each
  // into its real grid slot exactly once, in the SAME synchronous pre-paint
  // window React already uses to build any portaled content into them (a
  // portal's children are constructed during the normal commit regardless
  // of whether the target itself is yet attached to `document`), so there is
  // no visible flash either. The inline fallback in GameDashboard.tsx stays
  // — it's still correct and necessary for the case these vars are
  // genuinely, permanently null (every GameDashboard.*.test.tsx mocks
  // GameLayout out entirely, so `useShellSlots()` there returns
  // ShellSlotsContext's `{bandEl: null, deckEl: null}` default and NEVER
  // transitions — no flip risk in that case either, by construction).
  const [bandEl] = useState<HTMLDivElement>(() => document.createElement('div'));
  const [deckEl] = useState<HTMLDivElement>(() => document.createElement('div'));
  const bandSlotRef = useRef<HTMLDivElement>(null);
  const deckSlotRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const slot = bandSlotRef.current;
    if (!slot) return undefined;
    slot.appendChild(bandEl);
    return () => { slot.removeChild(bandEl); };
  }, [bandEl]);
  useLayoutEffect(() => {
    const slot = deckSlotRef.current;
    if (!slot) return undefined;
    slot.appendChild(deckEl);
    return () => { slot.removeChild(deckEl); };
  }, [deckEl]);
  const shellSlots = useMemo(() => ({ bandEl, deckEl }), [bandEl, deckEl]);

  // ── Mode classes (WO-UI0-PERSISTENT-SHELL lane B, ADDITIVE per ruling D3;
  // WO-UI0-SHELL-TRANSPLANT: `.mode-station`/`.mode-surface` are now ALSO
  // the band-height selector cockpit-shell.css's `.band` rules key off —
  // see game-layout.css's `.mode-station .band`/`.mode-surface .band`) ────
  // `mode-station` carries real styling (WO-UI3-STATION-MODE, game-layout.css)
  // — it drives the DOCKED "station face": GameDashboard renders no windshield
  // scene / deck-monitor bezel in this mode, and the descendant rules there
  // are scoped under `.game-container.mode-station` so `mode-flight`/
  // `mode-surface` are structurally untouched. Landed wins over docked
  // (unchanged precedence). The old manual windshield-minimize/expand toggle
  // (id=151) is RETIRED here — the guardrail it served (narrow-preserving a
  // full-bleed scene) is retired too; the band is now a fixed-height row per
  // mode (cockpit-shell.css), not a player-resizable one.
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
            console fill the lower area (right viewport column collapses).
            The companion .console-collapsed edge-toggle (manual hide-console
            + the WO-129-B landing auto-collapse) is RETIRED
            (WO-UI5-RETIREMENT+GLASS) — the v10 artifact's own shell has no
            collapse affordance at all (fixed rail, always); rail-peek was
            already retired earlier and logout lives in the HUD. */}
        {/* WO-UI0-SHELL-TRANSPLANT: `.game-container` IS the `.stage` grid
            host now (cockpit-shell.css targets `.stage, .game-container`
            with the same rule — an ALSO-selector, not a rename, see that
            file's own ADAPTATION(a) comment). Single-column grid, 4 rows
            (auto/auto/auto/1fr): StatusBar / `.band` / Teleprinter / `.lower`
            — SLOT DESIGN: none of these carry a `.sbar`/`.tele` classname
            themselves (that's the leaf lanes' reclass, per the WO — the
            shell CSS's `.sbar`/`.tele` rules stay inert until then); they
            land in their rows purely by DOM-order auto-placement. `.band`/
            `.lower > .deck` are real, empty, ref-published grid slots
            GameDashboard portals its windshield/console into (ShellSlots
            below); `.main-viewport`'s `{children}` (every /game/* route)
            stays a DIRECT, position:absolute grid child sized to rows 2-4
            via explicit `grid-row` (game-layout.css) — excluded from the
            auto-placement count (an abspos grid item with an explicit
            grid-row never consumes an auto-placed row), so it does NOT
            shift StatusBar/band/Teleprinter/lower off their intended rows
            despite being first in this JSX. */}
        <div
          className={`game-container ${mode}${
            playerState?.is_docked || playerState?.is_landed ? ' console-expand' : ''
          }${teleprinterBodyPanel ? ' tp-panel' : ''}`}
        >
          {/* MFDProvider (WO-UI1-CHROME-COMPLETE, widened again here) wraps
              the ENTIRE `.stage` content now — Annunciator (inside `.band`)
              and the MFD screens (inside `.lower .mfdcol`) both still need
              useMFD(), and MFDProvider renders ZERO DOM of its own (a bare
              context-provider pair), so widening its scope changes NOTHING
              about the grid: every one of these stays exactly the same
              direct `.game-container` grid-item child it would be without
              the provider wrapping it — same precedent as the last widening
              (see git history). ShellSlotsContext publishes the band/deck
              portal targets to whatever renders as `{children}`. */}
          <MFDProvider>
          <ShellSlotsContext.Provider value={shellSlots}>

          <main
            className="game-content"
            aria-busy={isInitialLoad}
            ref={mainLandmarkRef}
            tabIndex={-1}
          >
            {/* Children render UNCONDITIONALLY — never unmounted by a
                background refresh (see cockpit-stability note above).
                During the initial-load overlay the viewport is `inert`
                so its controls can't be tab-focused underneath.
                WO-UI5-RETIREMENT+GLASS: of the 11 nested /game/* routes
                (App.tsx), only the INDEX route (GameDashboard) still mounts
                real content here — its real content is portaled out of this
                box into `.band`/`.deck` below (ShellSlots), so what renders
                HERE for it is residual chrome only (alerts/modals, all
                position:fixed/portaled-to-body, indifferent to where this
                box sits). The other 10 legacy paths (map/team/governance/
                combat/planets/ships/player/trading/ranking/settings) are
                now bare client-side <Navigate> redirects back onto `/game`
                (GameRouteRedirects.tsx) — they flash through this box for a
                tick and never render real page content into it. */}
            <div
              className="main-viewport"
              inert={isInitialLoad}
            >
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
              .game-container/.stage, auto-placed into grid row 1. Supersedes
              PlayerVitalsHud (removed — WO-CLEANUP-PLAYERVITALSHUD). */}
          <StatusBar />

          {/* `.band` (WO-UI0-SHELL-TRANSPLANT) — the ambient scene row,
              auto-placed into grid row 2. Height is a fixed em value per
              mode (cockpit-shell.css `.band` base + game-layout.css's
              `.mode-station .band`/`.mode-surface .band`) — no longer
              player-resizable (the old windshield-minimize/expand toggle
              is retired, see the `mode` doc-comment above). Annunciator
              (WO-UI1-ANNUNCIATOR) mounts directly inside it as a NORMAL React
              child (unaffected by the eager-portal-target fix below — it's
              never conditionally portaled, so it never hits the type-flip
              bug that motivated it) — `.band` is already `position:relative`
              (cockpit-shell.css), exactly the ancestor Annunciator's own
              `position:absolute; inset:0` overlay needs; retires the old
              `.windshield-hud-anchor` wrapper. `bandSlotRef` is this row's
              OWN grid-cell node — the eagerly-created `bandEl` (above) is
              appended into it via `useLayoutEffect`, not rendered as a
              normal JSX child, so it sits alongside Annunciator without
              React ever trying to reconcile it (React only manages nodes it
              itself rendered; a manually-`appendChild`'d node is invisible
              to that reconciliation and is never touched by it as long as
              this row's OWN JSX children list — just `<Annunciator/>` —
              stays stable, which it does). */}
          <div className="band" ref={bandSlotRef}>
            <Annunciator />
          </div>

          {/* Teleprinter (WO-UI1-TELEPRINTER stitch) — a DIRECT, non-
              absolute child of .game-container/.stage, auto-placed into
              grid row 3. Both display toggles are CONTROLLED from here
              (WO-UI1-CHROME-COMPLETE; WO-UI-MAX-BATCH-1 REVISE) — see
              teleprinterBodyPanel's own doc-comment for why bodyPanel is
              owned here (the MFD-B fold below needs to see it). */}
          <Teleprinter
            bodyPanel={teleprinterBodyPanel}
            onBodyPanelChange={setTeleprinterBodyPanel}
            transcriptOpen={teleprinterTranscriptOpen}
            onTranscriptOpenChange={setTeleprinterTranscriptOpen}
          />

          {/* `.lower` (WO-UI0-SHELL-TRANSPLANT) — MFD column + instrument
              deck, auto-placed into grid row 4 (the `1fr` row — everything
              else is a fixed/content-sized row, this one absorbs whatever
              height is left). `.mfdcol` RELOCATES the old absolute
              `<aside>` sidebar's content here: RouteRail (the old
              ship-systems nav rail that used to top it) is RETIRED
              (WO-UI5-RETIREMENT+GLASS — its 9 nav keys are superseded by
              the 10 client-side legacy-route redirects in App.tsx, see
              GameRouteRedirects.tsx) — `.mfdcol` now holds ONLY the MFD
              screen(s), matching the v10 artifact anatomy (cockpit-
              shell.css's own untouched `.mfdcol { grid-template-rows: 1fr
              1fr }` base, no override needed for exactly 2 children). The
              `.folded` modifier switches `.mfdcol`'s row template
              (game-layout.css) between hosting 2 screens vs. the single
              mid-panel-folded config, same branch as before. `deckSlotRef`
              is this cell's OWN grid node — the eagerly-created `deckEl`
              (above) is appended into it via `useLayoutEffect`, the same
              manually-attached-node pattern `.band`/`bandEl` uses (this
              cell has zero normal JSX children of its own, so there's
              nothing for React to ever conflict with). GameDashboard's
              console portals its 3-monitor/station-face/surface-face
              content into `deckEl`. */}
          <div className="lower">
            <aside className={teleprinterBodyPanel ? 'mfdcol folded' : 'mfdcol'}>
              {teleprinterBodyPanel ? (
                <MFDScreen config={SIDEBAR_A_FOLDED} />
              ) : (
                <>
                  <MFDScreen config={SIDEBAR_A} />
                  <MFDScreen config={SIDEBAR_B} />
                </>
              )}
              <MFDAlertWiring />
            </aside>
            <div className="deck" ref={deckSlotRef} />
          </div>

          </ShellSlotsContext.Provider>
          </MFDProvider>
        </div>
      </div>
    </div>
    </ShellPresenceContext.Provider>
  );
};

export default GameLayout;
