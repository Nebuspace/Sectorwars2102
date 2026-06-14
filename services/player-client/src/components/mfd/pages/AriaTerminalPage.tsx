/**
 * ARIA TERMINAL — MFD ops page (NEON15 B3).
 *
 * Full-page extraction of the retired AriaConsoleStrip (the cockpit Law 4
 * bottom fixture). The page IS the surface now, so the strip's expand/
 * collapse drawer, unread badge, settings popover, voice input, and
 * recommendations panel are gone (parking lot). Everything that made ARIA
 * a navigator survives intact:
 *
 * - Message feed: WS ariaMessages merged with local navMessages, newest at
 *   the bottom, auto-scroll on append. Input pinned at the page bottom.
 * - ADR-0072 command grammar intercept (plot/goto/engage/abort) parsed
 *   BEFORE the WS send and resolved via AutopilotContext — ported verbatim.
 * - Autopilot state narration: engaged/paused/arrived transitions append
 *   local ARIA> log lines; per-hop updates go to the status ticker line only
 *   (rate discipline). Local nav messages never touch the WS pipe.
 *
 * Security posture ported intact from the strip:
 * - DOMPurify sanitization on all outbound input (XSS prevention)
 * - Input length limits, client-side rate limiting
 * - WebSocket transport only (no ad-hoc HTTP chat calls)
 */

import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import DOMPurify from 'dompurify';
import { useWebSocket } from '../../../contexts/WebSocketContext';
import { useAutopilot } from '../../../contexts/AutopilotContext';
import { MFDPageHeader, MFDPageBody } from '../atoms';
import { ariaFeed, useAriaFeed } from '../ariaFeedStore';
import './pages-ops.css';

const ACCENT = '#7B2FFF';

/** Action payload on an inbound ARIA message (loose upstream shape). */
type AriaAction = { type: string; [key: string]: unknown };

/**
 * Render an ARIA action's payload as a readable one-line summary — never a raw
 * JSON.stringify dump (that leaked braces/quotes into the flagship terminal).
 * Only primitive fields are surfaced; the type is shown separately as a label.
 */
const formatActionDetail = (action: AriaAction): string =>
  Object.entries(action)
    .filter(
      ([k, v]) =>
        k !== 'type' &&
        (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean')
    )
    .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${v}`)
    .join(' · ');

/** A feed entry — either a WS ariaMessages item or a local NavMessage. */
interface LogEntry {
  id: string;
  type: 'ai' | 'user';
  content: string;
  timestamp: string;
  conversationId?: string;
  confidence?: number;
  actions?: AriaAction[];
  suggestions?: string[];
  isNav?: true;
}

// ── Command grammar regexes (ADR-0072 §Pillar 3 — ported verbatim) ────────
const RE_PLOT_COURSE =
  /^(plot|lay in|set)\s+(a\s+)?(course|route)\s*(to|for)?\s*#?(\d+)$/i;
const RE_GOTO =
  /^(goto|navigate to)\s+#?(\d+)$/i;
const RE_ENGAGE =
  /^(engage|engage autopilot)$/i;
const RE_ABORT =
  /^(abort|all stop)$/i;

const MAX_MESSAGE_LENGTH = 4000;
const MIN_REQUEST_INTERVAL = 1000; // 1 second between requests
const MAX_REQUESTS_PER_MINUTE = 30;

const WELCOME_SUGGESTIONS = [
  "What's the best trade route right now?",
  'Help me plan my next strategic move',
  'Should I buy that port in sector 15?',
  'Analyze my combat readiness'
];

const AriaTerminalPage: React.FC = () => {
  // ── Terminal state ─────────────────────────────────────────────────
  const [inputValue, setInputValue] = useState('');
  const [isThinking, setIsThinking] = useState(false);

  // Feed state lives in the module store so it survives page switches
  // (and so MFDAlertWiring can narrate while this page is unmounted).
  const { navMessages, conversationId } = useAriaFeed();

  /** Ticker override for per-hop updates — cleared after 4 s */
  const [hopTickerText, setHopTickerText] = useState<string | null>(null);
  const hopTickerTimerRef = useRef<number | null>(null);

  // Client-side rate limiting (ported intact)
  const [rateLimitWarning, setRateLimitWarning] = useState(false);
  const [lastRequestTime, setLastRequestTime] = useState(0);
  const [requestCount, setRequestCount] = useState(0);

  // ── Refs ───────────────────────────────────────────────────────────
  const logEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const thinkingTimeoutRef = useRef<number | null>(null);

  // ── Context hooks ──────────────────────────────────────────────────
  const { sendARIAMessage, ariaMessages, clearARIAMessages, isConnected } = useWebSocket();
  const { course, lastPlot, status: apStatus, currentHopIndex, plotCourse, engage, abort } =
    useAutopilot();

  /**
   * pendingPlotRef: when the terminal issues a plotCourse() command, it
   * stores the requested sectorId here. The useEffect below watches lastPlot
   * and fires the narration reply when lastPlot changes and matches the
   * pending id. Reset to null after the reply is emitted.
   */
  const pendingPlotSectorRef = useRef<number | null>(null);

  // ── Helper: append a local nav ARIA> line ──────────────────────────
  // (The strip's unread bookkeeping is gone — page visibility is the MFD
  // alert system's concern now, wired at the MFDProvider level.)
  const appendNav = useCallback((content: string) => {
    ariaFeed.appendNav(content);
  }, []);

  // ── Auto-scroll the feed on append ─────────────────────────────────
  // block:'nearest' confines the scroll to the MFDPageBody container —
  // scrollIntoView must never yank the cockpit page itself.
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [ariaMessages, navMessages, isThinking]);

  // ── Thinking bookkeeping on inbound messages ───────────────────────
  useEffect(() => {
    if (ariaMessages.length === 0) return;
    const last = ariaMessages[ariaMessages.length - 1];
    if (last.type === 'ai') {
      setIsThinking(false);
      if (thinkingTimeoutRef.current !== null) {
        window.clearTimeout(thinkingTimeoutRef.current);
        thinkingTimeoutRef.current = null;
      }
    }
  }, [ariaMessages]);

  // ── Clear the timers on unmount ────────────────────────────────────
  useEffect(() => {
    return () => {
      if (thinkingTimeoutRef.current !== null) {
        window.clearTimeout(thinkingTimeoutRef.current);
      }
      if (hopTickerTimerRef.current !== null) {
        window.clearTimeout(hopTickerTimerRef.current);
      }
    };
  }, []);

  // ── ADR-0072 §B3 — Autopilot state narration ──────────────────────
  // Engaged/paused/arrived narration lives in MFDAlertWiring (GameLayout),
  // which never unmounts — transitions narrate into the feed store even
  // while another MFD-B page is shown. Only the per-hop ticker (a visual
  // transient) stays page-local.
  const prevHopIndexRef = useRef<number>(currentHopIndex);

  // Per-hop update → ticker only (not log)
  useEffect(() => {
    if (apStatus !== 'engaged') return;
    if (currentHopIndex === prevHopIndexRef.current) return;
    prevHopIndexRef.current = currentHopIndex;

    const totalHops = course?.hops?.length ?? 0;
    if (totalHops === 0) return;

    const hopSectorId = course?.hops?.[currentHopIndex - 1]?.sector_id ?? '?';
    const tickerLine = `HOP ${currentHopIndex}/${totalHops} — SECTOR ${hopSectorId}.`;

    // Set the ticker override and schedule its clearance
    setHopTickerText(tickerLine);
    if (hopTickerTimerRef.current !== null) {
      window.clearTimeout(hopTickerTimerRef.current);
    }
    hopTickerTimerRef.current = window.setTimeout(() => {
      setHopTickerText(null);
      hopTickerTimerRef.current = null;
    }, 4000);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentHopIndex, apStatus]);

  // ── Rate limiting (ported intact) ──────────────────────────────────
  const checkRateLimit = useCallback(() => {
    const now = Date.now();
    const timeSinceLastRequest = now - lastRequestTime;

    if (timeSinceLastRequest < MIN_REQUEST_INTERVAL) {
      setRateLimitWarning(true);
      setTimeout(() => setRateLimitWarning(false), 3000);
      return false;
    }

    const oneMinuteAgo = now - 60000;
    if (lastRequestTime < oneMinuteAgo) {
      setRequestCount(1);
    } else {
      setRequestCount(prev => prev + 1);
    }

    if (requestCount >= MAX_REQUESTS_PER_MINUTE) {
      setRateLimitWarning(true);
      setTimeout(() => setRateLimitWarning(false), 5000);
      return false;
    }

    setLastRequestTime(now);
    return true;
  }, [lastRequestTime, requestCount]);

  // ── Input sanitization (ported intact) ─────────────────────────────
  const sanitizeInput = useCallback((input: string): string => {
    let sanitized = DOMPurify.sanitize(input, { ALLOWED_TAGS: [] });
    sanitized = sanitized.replace(/[<>\"'`]/g, '');
    sanitized = sanitized.replace(/javascript:|data:|vbscript:/gi, '');
    return sanitized.slice(0, MAX_MESSAGE_LENGTH);
  }, []);

  // ── ADR-0072 §B3 — Reactive plot reply ───────────────────────────
  // plotCourse() stores its result in lastPlot (Promise<void>). We watch
  // lastPlot here; when it changes AND pendingPlotSectorRef is set, emit
  // the narration reply and clear the pending flag.
  useEffect(() => {
    const pending = pendingPlotSectorRef.current;
    if (pending === null || !lastPlot) return;

    // Only consume the result if the plot target matches what we requested
    // (guard against a race where another caller triggers plotCourse).
    if (lastPlot.target_sector_id !== pending) return;

    pendingPlotSectorRef.current = null;

    if (lastPlot.reachable) {
      const hopCount = lastPlot.hops?.length ?? 0;
      const turns = lastPlot.total_turns ?? '?';
      appendNav(
        `Course laid in for Sector ${pending} — ${hopCount} charted hop${hopCount !== 1 ? 's' : ''}, ${turns} turns. Say engage, or use the helm.`
      );
    } else if (lastPlot.reachable === false) {
      if (lastPlot.nearest_known) {
        appendNav(
          `Sector ${pending} is beyond my charts. Nearest charted approach is Sector ${lastPlot.nearest_known.sector_id}. Fly the frontier and I will learn the route.`
        );
      } else {
        appendNav(`No such sector on any chart I can read.`);
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastPlot]);

  // ── ADR-0072 §B3 — Command grammar intercept (ported verbatim) ────
  // Returns true if the input was intercepted (do NOT send to WS).
  const tryNavCommand = useCallback((raw: string): boolean => {
    const trimmed = raw.trim();

    // engage
    if (RE_ENGAGE.test(trimmed)) {
      engage();
      return true;
    }

    // abort / all stop
    if (RE_ABORT.test(trimmed)) {
      abort('voice command');
      return true;
    }

    // plot course to <n> / goto <n>
    let sectorId: number | null = null;
    const mPlot = trimmed.match(RE_PLOT_COURSE);
    const mGoto = trimmed.match(RE_GOTO);
    if (mPlot) {
      sectorId = parseInt(mPlot[5], 10);
    } else if (mGoto) {
      sectorId = parseInt(mGoto[2], 10);
    }

    if (sectorId !== null && !Number.isNaN(sectorId)) {
      // Append a YOU> echo (same way sendARIAMessage echoes the user line)
      ariaFeed.appendUserEcho(trimmed);

      // Store the pending sector ID; the lastPlot useEffect above fires the
      // reply when AutopilotContext resolves the plot request.
      pendingPlotSectorRef.current = sectorId;
      plotCourse(sectorId).catch(() => {
        // plotCourse threw (network error) — reply immediately
        pendingPlotSectorRef.current = null;
        appendNav(`No such sector on any chart I can read.`);
      });

      return true;
    }

    return false;
  }, [plotCourse, engage, abort, appendNav]);

  // ── Send via WebSocket (with nav command intercept) ─────────────────
  const sendMessage = useCallback(() => {
    if (!inputValue.trim() || isThinking || !checkRateLimit() || !isConnected) {
      if (!isConnected) {
        // Still allow nav commands offline (they don't use WS)
        const raw = inputValue.trim();
        if (raw && tryNavCommand(raw)) {
          setInputValue('');
          return;
        }
        setRateLimitWarning(true);
        setTimeout(() => setRateLimitWarning(false), 3000);
      }
      return;
    }

    const sanitizedMessage = sanitizeInput(inputValue.trim());
    if (!sanitizedMessage) {
      return;
    }

    // ── Nav command intercept — before the WS send ──────────────────
    if (tryNavCommand(sanitizedMessage)) {
      setInputValue('');
      return;
    }

    setIsThinking(true);

    // The strip's system-focus toggles did not survive the move to a full
    // page (settings popover dropped — parking lot). The strip shipped with
    // selectedSystems defaulting to ['trading'], so every message went out
    // with context 'trading' — preserve that default here.
    const success = sendARIAMessage(sanitizedMessage, conversationId ?? undefined, 'trading');

    if (success) {
      setInputValue('');
      if (!conversationId) {
        const newConversationId = `conv_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
        ariaFeed.setConversationId(newConversationId);
      }
    } else {
      console.error('Failed to send ARIA message via WebSocket');
    }

    // Safety timeout: stop the thinking pulse if no response arrives
    if (thinkingTimeoutRef.current !== null) {
      window.clearTimeout(thinkingTimeoutRef.current);
    }
    thinkingTimeoutRef.current = window.setTimeout(() => setIsThinking(false), 5000);
  }, [inputValue, isThinking, checkRateLimit, sanitizeInput, conversationId, sendARIAMessage, isConnected, tryNavCommand]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }, [sendMessage]);

  const clearConversation = useCallback(() => {
    clearARIAMessages();
    ariaFeed.clearNav();
    ariaFeed.setConversationId(null);
  }, [clearARIAMessages]);

  // ── Merged feed: ariaMessages + navMessages, ordered by timestamp ───
  const mergedMessages = useMemo<LogEntry[]>(() => {
    const all: LogEntry[] = [...ariaMessages, ...navMessages];
    // Defensive: a malformed inbound frame (e.g. an aria_response without a
    // timestamp) must never crash the terminal — coalesce to '' before compare.
    all.sort((a, b) => (a.timestamp ?? '').localeCompare(b.timestamp ?? ''));
    return all;
  }, [ariaMessages, navMessages]);

  // Status ticker line: per-hop override takes priority while set
  const tickerText = hopTickerText ?? (isConnected ? 'LINK OK' : 'UPLINK OFFLINE');

  return (
    <div className="mfd-page-ops mfd-page-aria">
      <MFDPageHeader title="ARIA TERMINAL" accent={ACCENT} status="shipped" />

      <MFDPageBody scrollKey="aria-terminal">
        <div className="mfd-page-aria-log" role="log" aria-live="polite">
          {mergedMessages.length === 0 && (
            <div className="mfd-page-aria-welcome">
              <div className="mfd-page-aria-line ai">
                <span className="mfd-page-aria-prefix">ARIA&gt;</span>
                <span className="mfd-page-aria-text">
                  Ship intelligence online. Strategic trading, combat tactics, colonization
                  planning — state your query, Commander.
                </span>
              </div>
              <div className="mfd-page-aria-chips">
                {WELCOME_SUGGESTIONS.map((suggestion, idx) => (
                  <button
                    key={idx}
                    type="button"
                    className="mfd-page-aria-chip"
                    onClick={() => setInputValue(suggestion)}
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          )}

          {mergedMessages.map((message) => (
            <div
              key={message.id}
              className={`mfd-page-aria-line ${message.type}${message.isNav ? ' nav' : ''}`}
            >
              <span className="mfd-page-aria-prefix">{message.type === 'ai' ? 'ARIA>' : 'YOU>'}</span>
              <span className="mfd-page-aria-text">
                {message.content}
                {message.type === 'ai' && typeof message.confidence === 'number' && (
                  <span className="mfd-page-aria-meta"> [{Math.round(message.confidence * 100)}%]</span>
                )}
              </span>
              {message.type === 'ai' && message.suggestions && message.suggestions.length > 0 && (
                <div className="mfd-page-aria-chips inline">
                  {message.suggestions.slice(0, 3).map((suggestion, idx) => (
                    <button
                      key={idx}
                      type="button"
                      className="mfd-page-aria-chip"
                      onClick={() => setInputValue(suggestion)}
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              )}
              {message.type === 'ai' && message.actions && message.actions.length > 0 && (
                <div className="mfd-page-aria-actions">
                  {message.actions.map((action, idx) => {
                    const detail = formatActionDetail(action);
                    return (
                      <div key={idx} className="mfd-page-aria-action">
                        <span className="mfd-page-aria-action-type">{action.type}</span>
                        {detail && (
                          <span className="mfd-page-aria-action-detail">{' '}{detail}</span>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          ))}

          {isThinking && (
            <div className="mfd-page-aria-line ai thinking">
              <span className="mfd-page-aria-prefix">ARIA&gt;</span>
              <span className="mfd-page-aria-text mfd-page-aria-thinking">
                PROCESSING<span className="mfd-page-aria-ellipsis" />
              </span>
            </div>
          )}

          <div ref={logEndRef} />
        </div>
      </MFDPageBody>

      {/* ── Pinned below the scroll body: status, warnings, input ── */}
      {rateLimitWarning && (
        <div className="mfd-page-aria-rate" role="status">
          ⚠ {isConnected ? 'TRANSMISSION RATE EXCEEDED — STAND BY' : 'UPLINK OFFLINE — MESSAGE NOT SENT'}
        </div>
      )}

      <div className="mfd-page-aria-status">
        <span className={`mfd-page-aria-status-text${hopTickerText ? ' hop' : ''}${isConnected ? '' : ' offline'}`}>
          {tickerText}
        </span>
        <span className="mfd-page-aria-count">{inputValue.length}/{MAX_MESSAGE_LENGTH}</span>
        {mergedMessages.length > 0 && (
          <button
            type="button"
            className="mfd-page-aria-key"
            onClick={clearConversation}
            aria-label="Clear conversation"
          >
            CLR
          </button>
        )}
      </div>

      <div className="mfd-page-aria-input-row">
        <span className="mfd-page-aria-prompt" aria-hidden="true">&gt;</span>
        <input
          ref={inputRef}
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="QUERY SHIP INTELLIGENCE…"
          maxLength={MAX_MESSAGE_LENGTH}
          className="mfd-page-aria-input"
          disabled={isThinking}
          aria-label="Message ARIA"
        />
        <button
          type="button"
          onClick={sendMessage}
          className="mfd-page-aria-key xmit"
          disabled={!inputValue.trim() || isThinking}
          aria-label="Send message"
        >
          XMIT
        </button>
      </div>
    </div>
  );
};

export default React.memo(AriaTerminalPage);
