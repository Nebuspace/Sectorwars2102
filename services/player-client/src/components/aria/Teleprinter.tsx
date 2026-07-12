/**
 * Teleprinter — the ARIA narration/dialogue box for the shell's reserved
 * `teleprinter` grid slot (WO-UI1-TELEPRINTER sub-part a;
 * game-layout.css:99-103 names the row, empty until the serial stitch that
 * also lands ANNUNCIATOR).
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
 *
 * The merged stream is a natural 3-way partition, which IS this component's
 * three modes (no new message shape invented):
 *   - narration    — isNarration (server catalog events) OR local isNav
 *                    ai-lines (autopilot transitions). Ambient prose, read-
 *                    primary; "every event, every lamp spoken in prose."
 *   - dialogue     — plain conversational turns (neither isNav nor
 *                    isNarration): aria_response + free player chat.
 *   - command-echo — local isNav user-lines (ariaFeed.appendUserEcho), the
 *                    terse "YOU>" echo for intercepted grammar-style input
 *                    that never round-trips the WS pipe.
 *
 * Input behavior is mode-aware but stays on the SAME two existing calls:
 * command-echo submits via ariaFeed.appendUserEcho (local, offline-safe,
 * mirrors AriaTerminalPage's tryNavCommand echo); narration/dialogue submit
 * via sendARIAMessage (which itself already appends the user's line into
 * ariaMessages on success — see WebSocketContext.tsx:380-396), falling back
 * to a component-local "pinned" echo (Pixel a11y REVISE #2) if the WS send
 * fails, tagged with the mode active at submit time so the line stays
 * visible in the tab the player typed it into — never silently dropped,
 * and never vanishing into an unrelated tab.
 *
 * NOT built here (left to later sub-parts / the stitch): ADR-0072 grammar
 * interception (plot/goto/engage/abort — AutopilotContext), MFD-B fold
 * (sub-part c), and mounting into GameLayout (the serial stitch step).
 *
 * Minimize is CSS-only (`.tp-minimized` toggles `.tp-body`'s display) — the
 * component tree is never conditionally unmounted, so mode/input/scroll
 * state all survive a minimize/restore cycle.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import DOMPurify from 'dompurify';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { ariaFeed, useAriaFeed } from '../mfd/ariaFeedStore';
import './teleprinter.css';

export type TeleprinterMode = 'narration' | 'dialogue' | 'command-echo';

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

const Teleprinter: React.FC = () => {
  const { ariaMessages, sendARIAMessage, isConnected } = useWebSocket();
  const { navMessages, conversationId } = useAriaFeed();

  const [mode, setMode] = useState<TeleprinterMode>('narration');
  const [minimized, setMinimized] = useState(false);
  const [inputValue, setInputValue] = useState('');
  const [inputFocused, setInputFocused] = useState(false);
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

  const submit = useCallback(() => {
    const raw = inputValue.trim();
    if (!raw) return;
    const sanitized = sanitizeInput(raw);
    if (!sanitized) {
      setInputValue('');
      return;
    }

    if (mode === 'command-echo') {
      // Terse local echo, never touches the WS pipe — matches
      // AriaTerminalPage's tryNavCommand echo for intercepted commands.
      ariaFeed.appendUserEcho(sanitized);
    } else {
      const success = sendARIAMessage(sanitized, conversationId ?? undefined, 'trading');
      if (success) {
        if (!conversationId) {
          ariaFeed.setConversationId(`conv_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`);
        }
      } else {
        // Offline fallback — a typed line must never silently vanish, and
        // (Pixel a11y REVISE #2) must stay visible in the mode the player
        // was actually typing into, not jump to command-echo.
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
  }, [inputValue, mode, sendARIAMessage, conversationId]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        submit();
      }
    },
    [submit]
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

  return (
    <div className={`teleprinter${minimized ? ' tp-minimized' : ''}`} data-testid="teleprinter">
      {/* Always-visible strip: doubles as the minimize/restore toggle and
          keeps the latest line visible unscrolled whether expanded or not
          (accept #1/#4). */}
      <button
        type="button"
        className="tp-strip-toggle"
        onClick={() => setMinimized((m) => !m)}
        aria-expanded={!minimized}
        aria-controls="tp-body"
        title={minimized ? 'Expand teleprinter' : 'Minimize teleprinter'}
      >
        <span className="tp-glyph" aria-hidden="true">{minimized ? '▸' : '▾'}</span>
        <span className="tp-strip-label">ARIA</span>
        <span className="tp-strip-line">{latestLine}</span>
        {!isConnected && <span className="tp-strip-offline">UPLINK OFFLINE</span>}
      </button>

      {/* Never conditionally unmounted — minimize is a pure CSS toggle on
          the parent's class (accept #4: preserve state, no remount). */}
      <div id="tp-body" className="tp-body">
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
