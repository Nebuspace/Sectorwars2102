/**
 * Teleprinter — the ARIA narration/dialogue/command box for the shell's
 * reserved `teleprinter` grid slot (WO-UI1-TELEPRINTER sub-part a;
 * game-layout.css:99-103 names the row).
 *
 * WO-UI1-CHROME-COMPLETE lands the three pieces the original stitch
 * deliberately left for later (see the file's own prior header):
 *   1. ADR-0072 GRAMMAR WIRING — the CMD channel (and the ticker's own
 *      compact input, see below) now PARSE + EXECUTE the command grammar
 *      client-side (dock/undock/land/lift off/set course to N/engage/
 *      abort/status/help — cockpit-redesign-v10 §05 L506) instead of
 *      just echoing. Unrecognized input falls through to the existing
 *      ARIA free-chat (sendARIAMessage) unchanged — this is deterministic
 *      client-side dispatch, NOT a new AI-safety surface; the WS
 *      free-chat path is untouched.
 *   2. THREE display modes (ticker / mid-panel / full-overlay), replacing
 *      the old binary minimize/expand — controlled by the parent
 *      (GameLayout owns `displayMode`, mirrors its existing windshield-
 *      min pattern) because mid-panel also drives the MFD-B→MFD-A fold,
 *      a decision GameLayout must see to swap which MFDScreen configs it
 *      renders.
 *   3. ARIA ABSORPTION — the MFD-B `aria-terminal` page is retired
 *      (mfdRegistry.tsx/sidebarScreens.ts/mfdTypes.ts); this component is
 *      now the only place free-chat + commands live. AriaTerminalPage.tsx
 *      stays on disk, unregistered (same retirement pattern as
 *      ThreatPage/SalvagePage, WO-UI2-DECK-RECONCILE).
 *
 * TICKER FORM (visual-form steer, mid-build, relayed from Max via the
 * orchestrator): the compact mode is ONE amber-on-dark row —
 * `▸ ARIA ✎ <latest event>` + an inline command input + [XMIT] + a single
 * mode-toggle button — not the old click-anywhere-to-expand strip. XMIT
 * dispatches through the SAME grammar-first path as the CMD tab (item 1).
 * This is a deliberate, sanctioned exception to the shipped ARIA cyan/
 * violet convention (teleprinter.css header) — amber, matching the v10
 * prototype's teleprinter demo palette, scoped to JUST the ticker row;
 * mid-panel/full-overlay keep the existing narration=violet/dialogue=cyan/
 * command-echo=amber tab convention untouched.
 *
 * DISPLAY-MODE CONTROL (WO-UI-MAX-BATCH-1, Max's authoritative ruling
 * #11/12 — supersedes the WAVE-2/CHROME-COMPLETE two-button-per-location
 * design above and below): a SINGLE 3-state toggle (ticker→mid-panel→
 * full-overlay→ticker, `DISPLAY_MODE_CYCLE`/`cycleDisplayMode` below)
 * replaces what used to be FOUR separate jump buttons split across two
 * locations — the ticker's own [◫ PANEL]/[▲ LOG] and `#tp-body`'s
 * [⤢ overlay-toggle]/[▾ TICKER]. The toggle's label always names the
 * CURRENT mode (TICKER/PANEL/LOG) and `aria-pressed` reflects "expanded"
 * (true whenever displayMode isn't 'ticker'). It renders as TWO DOM
 * instances — one inside the ticker row's `.telerow` (after XMIT, same
 * slot the old PANEL/LOG pair occupied), one inside `#tp-body`'s
 * `.tp-display-controls` (replacing the old overlay/ticker toggle pair)
 * — sharing the exact same cycle/label logic; the ticker/mid-panel CSS
 * display-toggle means only ONE is ever visible to the player at a time,
 * so from the player's perspective there is exactly one control. Mid-
 * panel's own composition correction (the band no longer shrinking) lives
 * in game-layout.css's own comment, not here.
 *
 * REUSES the existing ARIA plumbing verbatim — no new transport:
 *   - useWebSocket().ariaMessages / sendARIAMessage / isConnected — the WS
 *     channel that already carries BOTH conversational aria_response turns
 *     AND server-pushed ARIANarrationMessage catalog events (isNarration:
 *     true — WO-ARIA-NARRATE-KERNEL / ADR-0068), see WebSocketContext.tsx's
 *     onARIANarration handler.
 *   - ariaFeedStore's ariaFeed/useAriaFeed — the module-level store that
 *     survives page/component unmounts, carrying local autopilot narration
 *     (ariaFeed.appendNav, isNav:true type:'ai') and local command echoes
 *     (ariaFeed.appendUserEcho, isNav:true type:'user') that never touch the
 *     WS pipe. Identical merge idiom to AriaTerminalPage.tsx's
 *     mergedMessages (ariaMessages + navMessages, timestamp-sorted).
 *   - useGame() / useAutopilot() — the SAME station/planet/course actions
 *     GameDashboard's manual helm buttons and AriaTerminalPage's grammar
 *     already dispatch to; nothing reimplemented here.
 *
 * The merged stream is a natural 3-way partition, which IS the mid-panel/
 * full-overlay CONTENT mode (independent of the ticker/mid-panel/full-
 * overlay DISPLAY mode above — no new message shape invented):
 *   - narration    — isNarration (server catalog events) OR local isNav
 *                    ai-lines (autopilot transitions, command replies).
 *                    Ambient prose, read-primary; "every event, every
 *                    lamp spoken in prose."
 *   - dialogue     — plain conversational turns (neither isNav nor
 *                    isNarration): aria_response + free player chat.
 *   - command-echo — local isNav user-lines (ariaFeed.appendUserEcho), the
 *                    terse "YOU>" echo for intercepted grammar-style
 *                    input (typed via either the ticker's own input or
 *                    the CMD tab's input).
 *
 * Input behavior is mode-aware but stays on the SAME two existing calls:
 * command-echo (+ the ticker's input) tries the ADR-0072 grammar first,
 * falling through to sendARIAMessage exactly like narration/dialogue;
 * narration/dialogue submit via sendARIAMessage directly (which itself
 * already appends the user's line into ariaMessages on success — see
 * WebSocketContext.tsx:380-396), falling back to a component-local
 * "pinned" echo (Pixel a11y REVISE #2) if the WS send fails, tagged with
 * the mode active at submit time so the line stays visible in the tab the
 * player typed it into — never silently dropped, and never vanishing into
 * an unrelated tab.
 *
 * Minimize/expand is CSS-only (the root's `tp-<displayMode>` class toggles
 * which of `.tp-ticker-row` / `.tp-body` is visible) — the component tree
 * is never conditionally unmounted, so mode/input/scroll state all
 * survive a display-mode switch.
 *
 * WO-UI0-SHELL-TRANSPLANT (leaf L4) re-classes the ticker row onto the
 * artifact's `.tele/.glyph/.tline/.telerow/.tin/.tkey` (cockpit-shell.css)
 * — see teleprinter.css's own header for the skin-ownership split and the
 * `.midlog`/`.telelog` mapping onto the mid-panel/full-overlay body. Pure
 * re-class + one data-correctness fix (`toEpoch` below); the grammar/modes/
 * a11y this header documents are untouched.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import DOMPurify from 'dompurify';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { useGame } from '../../contexts/GameContext';
import { useAutopilot } from '../../contexts/AutopilotContext';
import { ariaFeed, useAriaFeed } from '../mfd/ariaFeedStore';
import './teleprinter.css';

/** Content-channel tab, independent of the display-mode axis below. */
export type TeleprinterMode = 'narration' | 'dialogue' | 'command-echo';

/** Display-mode axis (cockpit-redesign-v10 §05 L624): how much of the
 *  cockpit the teleprinter occupies. `mid-panel` also drives the MFD-B→
 *  MFD-A fold in GameLayout — controlled from there, not owned locally. */
export type TeleprinterDisplayMode = 'ticker' | 'mid-panel' | 'full-overlay';

interface TeleprinterProps {
  displayMode: TeleprinterDisplayMode;
  onDisplayModeChange: (mode: TeleprinterDisplayMode) => void;
}

/** A feed entry — either a WS ariaMessages item, a local ariaFeedStore
 *  NavMessage, or a component-local offline-fallback echo. Mirrors
 *  AriaTerminalPage.tsx's LogEntry: the public WebSocketContextType.
 *  ariaMessages type doesn't declare isNarration (it's added structurally
 *  at runtime by the onARIANarration handler), so this local shape
 *  re-declares it as optional — assignable from both sources without a
 *  cast. pinnedMode (Pixel a11y REVISE #2) overrides the natural
 *  narration/dialogue/command-echo partition for offline-fallback lines,
 *  so they render in the mode the player was actually typing into. */
interface FeedEntry {
  id: string;
  type: 'ai' | 'user';
  content: string;
  timestamp: string;
  isNav?: true;
  isNarration?: true;
  pinnedMode?: TeleprinterMode;
}

/** Which mode a feed entry belongs under. pinnedMode (offline-fallback
 *  echoes) always wins; otherwise the natural 3-way partition of the
 *  reused ariaMessages/navMessages shape applies — see the module
 *  doc-comment. */
const inMode = (entry: FeedEntry, target: TeleprinterMode): boolean => {
  if (entry.pinnedMode) return entry.pinnedMode === target;
  switch (target) {
    case 'narration':
      return !!entry.isNarration || (!!entry.isNav && entry.type === 'ai');
    case 'command-echo':
      return !!entry.isNav && entry.type === 'user';
    case 'dialogue':
    default:
      return !entry.isNav && !entry.isNarration;
  }
};

/** Chronological key for the merge sort (n2, WO-UI0-SHELL-TRANSPLANT leaf
 *  L4) — a live ticker was observed stuck on an older line while a newer
 *  "Arrival: Sector N" nav entry already existed. Root cause: the two
 *  timestamp sources are NOT string-comparable. Client entries (both
 *  ariaMessages' `new Date().toISOString()` fallback and every ariaFeedStore
 *  nav line) are 'Z'-suffixed ('...123Z'); server-pushed narration `ts`
 *  (aria_narration_service.NarrationLine.to_payload, `created_at.isoformat()`
 *  on a tz-aware UTC datetime) is '+00:00'-suffixed microseconds
 *  ('...123456+00:00'). 'Z' (0x5A) sorts ABOVE any digit in a lexicographic
 *  compare, so `.localeCompare` on the raw strings biases every 'Z' entry
 *  "later" than a same-instant '+00:00' one regardless of the real order —
 *  exactly the kind of same-second race a busy ticker hits constantly.
 *  `Date.parse` normalizes both offset notations to the same epoch ms. */
const toEpoch = (ts?: string): number => {
  if (!ts) return 0;
  const parsed = Date.parse(ts);
  return Number.isNaN(parsed) ? 0 : parsed;
};

const MODES: Array<{ id: TeleprinterMode; label: string }> = [
  { id: 'narration', label: 'NARRATION' },
  { id: 'dialogue', label: 'DIALOGUE' },
  { id: 'command-echo', label: 'CMD' },
];

/** The single 3-state display-mode cycle (WO-UI-MAX-BATCH-1) — ticker →
 *  mid-panel → full-overlay → ticker, one step per click, wrapping. Order
 *  matches Max's authoritative wording (ticker/PANEL/LOG). */
const DISPLAY_MODE_CYCLE: TeleprinterDisplayMode[] = ['ticker', 'mid-panel', 'full-overlay'];

/** User-facing labels for the toggle — "the label shows the CURRENT
 *  mode" (Max's wording). Internal state/CSS class names are unchanged
 *  ('mid-panel'/'full-overlay') to avoid an unrelated cross-file rename;
 *  only the visible/announced text uses Max's PANEL/LOG vocabulary. */
const DISPLAY_MODE_LABEL: Record<TeleprinterDisplayMode, string> = {
  ticker: 'TICKER',
  'mid-panel': 'PANEL',
  'full-overlay': 'LOG',
};

const MAX_MESSAGE_LENGTH = 4000;

const EMPTY_TEXT: Record<TeleprinterMode, string> = {
  narration: 'No events narrated yet.',
  dialogue: 'Say something, Commander.',
  'command-echo': 'No commands logged yet.',
};

/** Outbound sanitization — ported verbatim from AriaTerminalPage.tsx's
 *  sanitizeInput (DOMPurify + tag-char strip + javascript:/data:/vbscript:
 *  strip), so the teleprinter's free-text channel gets the same XSS
 *  hardening as the terminal page it shares a transport with. */
const sanitizeInput = (input: string): string => {
  let sanitized = DOMPurify.sanitize(input, { ALLOWED_TAGS: [] });
  sanitized = sanitized.replace(/[<>"'`]/g, '');
  sanitized = sanitized.replace(/javascript:|data:|vbscript:/gi, '');
  return sanitized.slice(0, MAX_MESSAGE_LENGTH);
};

// ── ADR-0072 command grammar (cockpit-redesign-v10 §05 L506) ──────────────
// engage/abort/plot/goto ported verbatim from AriaTerminalPage.tsx's
// tryNavCommand (the terminal this component absorbs); dock/undock/land/
// lift-off/status/help are new, filling out the full canon grammar.
const RE_DOCK = /^dock$/i;
const RE_UNDOCK = /^undock$/i;
const RE_LAND = /^land$/i;
const RE_LIFTOFF = /^(lift[\s-]?off)$/i;
const RE_STATUS = /^status$/i;
const RE_HELP = /^help$/i;
const RE_ENGAGE = /^(engage|engage autopilot)$/i;
const RE_ABORT = /^(abort|all stop)$/i;
const RE_PLOT_COURSE =
  /^(plot|lay in|set)\s+(a\s+)?(course|route)\s*(to|for)?\s*#?(\d+)$/i;
const RE_GOTO = /^(goto|navigate to)\s+#?(\d+)$/i;

const Teleprinter: React.FC<TeleprinterProps> = ({ displayMode, onDisplayModeChange }) => {
  const { ariaMessages, sendARIAMessage, isConnected } = useWebSocket();
  const { navMessages, conversationId } = useAriaFeed();
  const {
    playerState,
    currentSector,
    stationsInSector,
    planetsInSector,
    dockAtStation,
    undockFromStation,
    landOnPlanet,
    leavePlanet,
  } = useGame();
  const { plotCourse, engage, abort: autopilotAbort } = useAutopilot();

  const [mode, setMode] = useState<TeleprinterMode>('narration');
  const [inputValue, setInputValue] = useState('');
  const [inputFocused, setInputFocused] = useState(false);
  const [tickerInputValue, setTickerInputValue] = useState('');
  // Offline-fallback echoes (Pixel a11y REVISE #2) — component-local, never
  // sent anywhere, pinned to the mode active when sendARIAMessage failed.
  const [localEchoes, setLocalEchoes] = useState<FeedEntry[]>([]);

  const logEndRef = useRef<HTMLDivElement>(null);
  // Roving-tabindex targets for the mode tablist's keyboard nav (Pixel a11y
  // REVISE #1) — one ref per rendered tab, same idiom as StatusBar.tsx's
  // dossier tabRefs.
  const modeTabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // Same merge idiom as AriaTerminalPage.tsx's mergedMessages, extended
  // with the local offline-fallback echoes.
  const merged = useMemo<FeedEntry[]>(() => {
    const all: FeedEntry[] = [...ariaMessages, ...navMessages, ...localEchoes];
    all.sort((a, b) => toEpoch(a.timestamp) - toEpoch(b.timestamp));
    return all;
  }, [ariaMessages, navMessages, localEchoes]);

  const filtered = useMemo<FeedEntry[]>(
    () => merged.filter((m) => inMode(m, mode)),
    [merged, mode]
  );

  const latestLine = merged.length > 0 ? merged[merged.length - 1].content : 'Standing by, Commander.';

  // Auto-scroll WITHIN the log panel only (block:'nearest' confines the
  // scroll to the nearest scrollable ancestor — the log body — never the
  // page), same guard AriaTerminalPage uses.
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [filtered]);

  // ── ADR-0072 command grammar intercept ────────────────────────────────
  // Returns true if the input was a recognized command (echoed + executed
  // locally — never reaches the WS). Returns false so the caller falls
  // through to the existing ARIA free-chat.
  const tryCommand = useCallback((raw: string): boolean => {
    const trimmed = raw.trim();

    if (RE_DOCK.test(trimmed)) {
      ariaFeed.appendUserEcho(trimmed);
      if (playerState?.is_docked) {
        ariaFeed.appendNav('Already docked, Commander.');
        return true;
      }
      const station = stationsInSector[0];
      if (!station) {
        ariaFeed.appendNav('No station in this sector to dock at.');
        return true;
      }
      autopilotAbort('manual helm action');
      dockAtStation(station.id)
        .then((result: { full?: boolean; detail?: string } | undefined) => {
          ariaFeed.appendNav(
            result?.full
              ? (result.detail || 'All docking slips are occupied — queued.')
              : `Docked at ${station.name}, Commander.`
          );
        })
        .catch(() => ariaFeed.appendNav('Docking sequence failed.'));
      return true;
    }

    if (RE_UNDOCK.test(trimmed)) {
      ariaFeed.appendUserEcho(trimmed);
      if (!playerState?.is_docked) {
        ariaFeed.appendNav('Not docked, Commander.');
        return true;
      }
      autopilotAbort('manual helm action');
      undockFromStation()
        .then(() => ariaFeed.appendNav('Undocked. Clear of the berth.'))
        .catch(() => ariaFeed.appendNav('Undocking failed.'));
      return true;
    }

    if (RE_LAND.test(trimmed)) {
      ariaFeed.appendUserEcho(trimmed);
      if (playerState?.is_landed) {
        ariaFeed.appendNav('Already landed, Commander.');
        return true;
      }
      const planet = planetsInSector[0];
      if (!planet) {
        ariaFeed.appendNav('No planet in this sector to land on.');
        return true;
      }
      autopilotAbort('manual helm action');
      landOnPlanet(planet.id)
        .then(() => ariaFeed.appendNav(`Landed on ${planet.name}, Commander.`))
        .catch(() => ariaFeed.appendNav('Landing sequence failed.'));
      return true;
    }

    if (RE_LIFTOFF.test(trimmed)) {
      ariaFeed.appendUserEcho(trimmed);
      if (!playerState?.is_landed) {
        ariaFeed.appendNav('Not landed, Commander.');
        return true;
      }
      autopilotAbort('manual helm action');
      leavePlanet()
        .then(() => ariaFeed.appendNav('Lifted off. Clear of the surface.'))
        .catch(() => ariaFeed.appendNav('Lift-off failed.'));
      return true;
    }

    if (RE_ENGAGE.test(trimmed)) {
      ariaFeed.appendUserEcho(trimmed);
      engage();
      return true;
    }

    if (RE_ABORT.test(trimmed)) {
      ariaFeed.appendUserEcho(trimmed);
      autopilotAbort('teleprinter command');
      return true;
    }

    if (RE_STATUS.test(trimmed)) {
      ariaFeed.appendUserEcho(trimmed);
      const sectorName = currentSector?.name || `Sector ${playerState?.current_sector_id ?? '?'}`;
      const posture = playerState?.is_docked ? 'DOCKED' : playerState?.is_landed ? 'LANDED' : 'IN FLIGHT';
      ariaFeed.appendNav(
        `Status: ${sectorName} — ${posture}. ${playerState?.turns ?? 0} turns, ${playerState?.credits ?? 0} credits banked.`
      );
      return true;
    }

    if (RE_HELP.test(trimmed)) {
      ariaFeed.appendUserEcho(trimmed);
      ariaFeed.appendNav(
        'Commands: dock · undock · land · lift off · set course to N · engage · abort · status · help.'
      );
      return true;
    }

    // set course to N / plot / lay in / goto — ported verbatim from
    // AriaTerminalPage.tsx's tryNavCommand.
    let sectorId: number | null = null;
    const mPlot = trimmed.match(RE_PLOT_COURSE);
    const mGoto = trimmed.match(RE_GOTO);
    if (mPlot) {
      sectorId = parseInt(mPlot[5], 10);
    } else if (mGoto) {
      sectorId = parseInt(mGoto[2], 10);
    }

    if (sectorId !== null && !Number.isNaN(sectorId)) {
      ariaFeed.appendUserEcho(trimmed);
      plotCourse(sectorId).catch(() => {
        ariaFeed.appendNav('No such sector on any chart I can read.');
      });
      return true;
    }

    return false;
  }, [
    playerState,
    currentSector,
    stationsInSector,
    planetsInSector,
    dockAtStation,
    undockFromStation,
    landOnPlanet,
    leavePlanet,
    autopilotAbort,
    engage,
    plotCourse,
  ]);

  // Shared dispatch: grammar first, ARIA free-chat fallback — used by both
  // the CMD tab's input and the ticker's own compact input (visual-form
  // steer), so XMIT means the same thing in either place.
  const dispatchLine = useCallback((sanitized: string, fallbackPinnedMode: TeleprinterMode) => {
    if (tryCommand(sanitized)) return;

    const success = sendARIAMessage(sanitized, conversationId ?? undefined, 'trading');
    if (success) {
      if (!conversationId) {
        ariaFeed.setConversationId(`conv_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`);
      }
    } else {
      // Offline fallback — a typed line must never silently vanish, and
      // (Pixel a11y REVISE #2) must stay visible in the mode the player
      // was actually typing into.
      setLocalEchoes((prev) => [
        ...prev,
        {
          id: `tp-offline-${Date.now()}-${prev.length}`,
          type: 'user',
          content: sanitized,
          timestamp: new Date().toISOString(),
          pinnedMode: fallbackPinnedMode,
        },
      ]);
    }
  }, [tryCommand, sendARIAMessage, conversationId]);

  // ── tp-body input (narration/dialogue/CMD tabs) ────────────────────────
  const submit = useCallback(() => {
    const raw = inputValue.trim();
    if (!raw) return;
    const sanitized = sanitizeInput(raw);
    if (!sanitized) {
      setInputValue('');
      return;
    }

    if (mode === 'command-echo') {
      dispatchLine(sanitized, 'command-echo');
    } else {
      const success = sendARIAMessage(sanitized, conversationId ?? undefined, 'trading');
      if (success) {
        if (!conversationId) {
          ariaFeed.setConversationId(`conv_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`);
        }
      } else {
        setLocalEchoes((prev) => [
          ...prev,
          {
            id: `tp-offline-${Date.now()}-${prev.length}`,
            type: 'user',
            content: sanitized,
            timestamp: new Date().toISOString(),
            pinnedMode: mode,
          },
        ]);
      }
    }
    setInputValue('');
  }, [inputValue, mode, dispatchLine, sendARIAMessage, conversationId]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        submit();
      }
    },
    [submit]
  );

  // ── Ticker's own compact input (visual-form steer) — always grammar-
  // first, exactly like the CMD tab; unrecognized text falls through to
  // free-chat, pinned to command-echo so it lands in the CMD tab once the
  // player expands. ────────────────────────────────────────────────────
  const submitTicker = useCallback(() => {
    const raw = tickerInputValue.trim();
    if (!raw) return;
    const sanitized = sanitizeInput(raw);
    if (!sanitized) {
      setTickerInputValue('');
      return;
    }
    dispatchLine(sanitized, 'command-echo');
    setTickerInputValue('');
  }, [tickerInputValue, dispatchLine]);

  const handleTickerKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        submitTicker();
      }
    },
    [submitTicker]
  );

  const placeholder =
    mode === 'command-echo' ? 'enter command — try: status' : 'speak to the ship';

  // Mode-blind label fix (Pixel a11y REVISE #3) — matches the placeholder,
  // which already varies per mode.
  const inputAriaLabel =
    mode === 'command-echo' ? 'Command ARIA' : mode === 'narration' ? 'Narration ARIA' : 'Message ARIA';

  // WAI-ARIA tabs pattern keyboard nav (Pixel a11y REVISE #1 — same class
  // as StatusBar.tsx's dossier handleTablistKeyDown): Left/Right cycle
  // tabs (wrapping), Home/End jump to first/last; each moves BOTH the
  // active mode and DOM focus to the newly-active tab (roving tabindex,
  // already set on the tab buttons below).
  const handleModeTablistKeyDown = useCallback((e: React.KeyboardEvent<HTMLDivElement>) => {
    const currentIndex = MODES.findIndex((m) => m.id === mode);
    let nextIndex = currentIndex;
    switch (e.key) {
      case 'ArrowRight':
        nextIndex = (currentIndex + 1) % MODES.length;
        break;
      case 'ArrowLeft':
        nextIndex = (currentIndex - 1 + MODES.length) % MODES.length;
        break;
      case 'Home':
        nextIndex = 0;
        break;
      case 'End':
        nextIndex = MODES.length - 1;
        break;
      default:
        return;
    }
    e.preventDefault();
    setMode(MODES[nextIndex].id);
    modeTabRefs.current[nextIndex]?.focus();
  }, [mode]);

  // ── Display-mode control — single 3-state toggle (WO-UI-MAX-BATCH-1) ───
  const nextDisplayMode = useMemo(
    () => DISPLAY_MODE_CYCLE[(DISPLAY_MODE_CYCLE.indexOf(displayMode) + 1) % DISPLAY_MODE_CYCLE.length],
    [displayMode]
  );
  const cycleDisplayMode = useCallback(
    () => onDisplayModeChange(nextDisplayMode),
    [nextDisplayMode, onDisplayModeChange]
  );
  const modeToggleAriaLabel = `Teleprinter display: ${DISPLAY_MODE_LABEL[displayMode]}. Activate to switch to ${DISPLAY_MODE_LABEL[nextDisplayMode]}.`;
  const modeToggleTitle = `${DISPLAY_MODE_LABEL[displayMode]} — click for ${DISPLAY_MODE_LABEL[nextDisplayMode]}`;

  return (
    <div className={`teleprinter tele tp-${displayMode}`} data-testid="teleprinter">
      {/* ── TICKER — one amber-on-dark row (visual-form steer), re-classed
          onto the artifact's cockpit-shell.css primitives (WO-UI0-SHELL-
          TRANSPLANT leaf L4): .glyph/.tline live directly in the row;
          .telerow wraps the input + 3 keys (cockpit-shell's .telerow is
          `display:contents` outside the artifact's own aria=2 mode, so it's
          a purely organizational wrapper here — zero layout change). Always
          in the DOM (CSS display-toggled, never unmounted) so a half-typed
          command survives a switch to mid-panel/full-overlay and back. ── */}
      <div className="tp-ticker-row" role="group" aria-label="ARIA teleprinter ticker">
        <span className="glyph" aria-hidden="true">▸ ARIA</span>
        <span className="tline" aria-live="polite">{latestLine}</span>
        {!isConnected && <span className="tp-ticker-offline">UPLINK OFFLINE</span>}
        <div className="telerow">
          <input
            type="text"
            className="tin"
            value={tickerInputValue}
            onChange={(e) => setTickerInputValue(e.target.value)}
            onKeyDown={handleTickerKeyDown}
            placeholder="speak to the ship — try: help"
            maxLength={MAX_MESSAGE_LENGTH}
            aria-label="Send command or message to ARIA"
          />
          <button
            type="button"
            className="tkey tp-ticker-xmit"
            onClick={submitTicker}
            disabled={!tickerInputValue.trim()}
            aria-label="Transmit"
          >
            XMIT
          </button>
          <button
            type="button"
            className="tkey tp-mode-toggle"
            onClick={cycleDisplayMode}
            aria-pressed={displayMode !== 'ticker'}
            aria-label={modeToggleAriaLabel}
            title={modeToggleTitle}
          >
            {DISPLAY_MODE_LABEL[displayMode]}
          </button>
        </div>
      </div>

      {/* ── mid-panel / full-overlay body — narration/dialogue/CMD tabs +
          log + input, unchanged from the prior single "expanded" state
          except for the CMD grammar wiring above and the single mode-
          toggle control below (WO-UI-MAX-BATCH-1). Never conditionally
          unmounted (accept #4/#5's state-preservation contract). ── */}
      {/* `telelog` (full-overlay only) borrows cockpit-shell.css's transcript-
          panel skin (warm-olive background, position:absolute/bottom:100%
          "opens upward", border-top) — see teleprinter.css's own comment for
          why the open-height is driven by `tp-full-overlay` rather than the
          artifact's `.stage.tele-open` (an ancestor class outside this leaf's
          file lane). */}
      <div
        id="tp-body"
        className={`tp-body${displayMode === 'full-overlay' ? ' telelog' : ''}`}
      >
        <div className="tp-body-header">
          <div
            className="tp-modes"
            role="tablist"
            aria-label="Teleprinter mode"
            onKeyDown={handleModeTablistKeyDown}
          >
            {MODES.map((m, i) => (
              <button
                key={m.id}
                type="button"
                role="tab"
                id={`tp-mode-tab-${m.id}`}
                ref={(el) => { modeTabRefs.current[i] = el; }}
                aria-selected={mode === m.id}
                aria-controls="tp-log"
                tabIndex={mode === m.id ? 0 : -1}
                className={`tp-mode-btn tp-mode-${m.id}${mode === m.id ? ' active' : ''}`}
                onClick={() => setMode(m.id)}
              >
                {m.label}
              </button>
            ))}
          </div>

          <div className="tp-display-controls">
            <button
              type="button"
              className="tp-display-btn tp-mode-toggle"
              onClick={cycleDisplayMode}
              aria-pressed={displayMode !== 'ticker'}
              aria-label={modeToggleAriaLabel}
              title={modeToggleTitle}
            >
              {DISPLAY_MODE_LABEL[displayMode]}
            </button>
          </div>
        </div>

        <div
          id="tp-log"
          className={`tp-log tp-log-${mode}${displayMode === 'mid-panel' ? ' midlog' : ''}`}
          role="log"
          aria-live="polite"
          aria-labelledby={`tp-mode-tab-${mode}`}
        >
          {filtered.length === 0 && (
            <div className="tp-line tp-empty">
              <span className="tp-prefix">ARIA&gt;</span>
              <span className="tp-text">{EMPTY_TEXT[mode]}</span>
            </div>
          )}
          {filtered.map((entry) => (
            <div
              key={entry.id}
              className={`tp-line ${entry.type}${entry.isNav ? ' nav' : ''}${entry.isNarration ? ' narration' : ''}`}
            >
              <span className="tp-prefix">{entry.type === 'ai' ? 'ARIA>' : 'YOU>'}</span>
              <span className="tp-text">{entry.content}</span>
            </div>
          ))}
          <div ref={logEndRef} />
        </div>

        <div className="tp-input-row">
          <span className="tp-prompt" aria-hidden="true">&gt;</span>
          <input
            type="text"
            className={`tp-input${inputFocused ? ' tp-input-focused' : ''}`}
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            onFocus={() => setInputFocused(true)}
            onBlur={() => setInputFocused(false)}
            placeholder={placeholder}
            maxLength={MAX_MESSAGE_LENGTH}
            aria-label={inputAriaLabel}
          />
          <button
            type="button"
            className="tp-xmit"
            onClick={submit}
            disabled={!inputValue.trim()}
            aria-label="Transmit"
          >
            XMIT
          </button>
        </div>
      </div>
    </div>
  );
};

export default React.memo(Teleprinter);
