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
 * `▸ ARIA ✎ <latest event>` + an inline command input + [XMIT] [◫ PANEL]
 * [▲ LOG] — not the old click-anywhere-to-expand strip. XMIT dispatches
 * through the SAME grammar-first path as the CMD tab (item 1); PANEL/LOG
 * jump straight to mid-panel/full-overlay. This is a deliberate,
 * sanctioned exception to the shipped ARIA cyan/violet convention
 * (teleprinter.css header) — amber, matching the v10 prototype's
 * teleprinter demo palette, scoped to JUST the ticker row; mid-panel/
 * full-overlay keep the existing narration=violet/dialogue=cyan/
 * command-echo=amber tab convention untouched.
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

const MODES: Array<{ id: TeleprinterMode; label: string }> = [
  { id: 'narration', label: 'NARRATION' },
  { id: 'dialogue', label: 'DIALOGUE' },
  { id: 'command-echo', label: 'CMD' },
];

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
    all.sort((a, b) => (a.timestamp ?? '').localeCompare(b.timestamp ?? ''));
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

  // ── Display-mode controls ──────────────────────────────────────────────
  const collapseToTicker = useCallback(() => onDisplayModeChange('ticker'), [onDisplayModeChange]);
  const toggleOverlay = useCallback(
    () => onDisplayModeChange(displayMode === 'full-overlay' ? 'mid-panel' : 'full-overlay'),
    [displayMode, onDisplayModeChange]
  );
  const openMidPanel = useCallback(() => onDisplayModeChange('mid-panel'), [onDisplayModeChange]);
  const openOverlay = useCallback(() => onDisplayModeChange('full-overlay'), [onDisplayModeChange]);

  return (
    <div className={`teleprinter tp-${displayMode}`} data-testid="teleprinter">
      {/* ── TICKER — one amber-on-dark row (visual-form steer). Always in
          the DOM (CSS display-toggled, never unmounted) so a half-typed
          command survives a switch to mid-panel/full-overlay and back. ── */}
      <div className="tp-ticker-row" role="group" aria-label="ARIA teleprinter ticker">
        <span className="tp-ticker-glyph" aria-hidden="true">▸</span>
        <span className="tp-ticker-label">ARIA</span>
        <span className="tp-ticker-pencil" aria-hidden="true">✎</span>
        <span className="tp-ticker-line" aria-live="polite">{latestLine}</span>
        {!isConnected && <span className="tp-ticker-offline">UPLINK OFFLINE</span>}
        <input
          type="text"
          className="tp-ticker-input"
          value={tickerInputValue}
          onChange={(e) => setTickerInputValue(e.target.value)}
          onKeyDown={handleTickerKeyDown}
          placeholder="speak to the ship — try: help"
          maxLength={MAX_MESSAGE_LENGTH}
          aria-label="Send command or message to ARIA"
        />
        <button
          type="button"
          className="tp-ticker-btn tp-ticker-xmit"
          onClick={submitTicker}
          disabled={!tickerInputValue.trim()}
          aria-label="Transmit"
        >
          XMIT
        </button>
        <button
          type="button"
          className="tp-ticker-btn tp-ticker-panel"
          onClick={openMidPanel}
          aria-label="Open teleprinter mid-panel"
        >
          ◫ PANEL
        </button>
        <button
          type="button"
          className="tp-ticker-btn tp-ticker-log"
          onClick={openOverlay}
          aria-label="Open teleprinter transcript overlay"
        >
          ▲ LOG
        </button>
      </div>

      {/* ── mid-panel / full-overlay body — narration/dialogue/CMD tabs +
          log + input, unchanged from the prior single "expanded" state
          except for the CMD grammar wiring above and the two display-
          mode controls below. Never conditionally unmounted (accept
          #4/#5's state-preservation contract). ── */}
      <div id="tp-body" className="tp-body">
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
              className="tp-display-btn tp-display-overlay-toggle"
              onClick={toggleOverlay}
              aria-label={displayMode === 'full-overlay' ? 'Collapse to mid-panel' : 'Expand to transcript overlay'}
              title={displayMode === 'full-overlay' ? 'Collapse to mid-panel' : 'Expand to transcript overlay'}
            >
              {displayMode === 'full-overlay' ? '▾' : '⤢'}
            </button>
            <button
              type="button"
              className="tp-display-btn tp-display-ticker-toggle"
              onClick={collapseToTicker}
              aria-label="Collapse to ticker"
              title="Collapse to ticker"
            >
              ▾ TICKER
            </button>
          </div>
        </div>

        <div
          id="tp-log"
          className={`tp-log tp-log-${mode}`}
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
