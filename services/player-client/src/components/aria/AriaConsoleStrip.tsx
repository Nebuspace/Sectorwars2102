/**
 * AriaConsoleStrip — ARIA docked as a console fixture (cockpit Law 4).
 *
 * A slim 36px strip that lives at the BOTTOM of the content region on every
 * /game route (the shell reserves the slot). Left: the ARIA core glyph
 * (steady cyan idle, pulsing violet while processing, unread badge when a
 * response arrives collapsed). Center: a one-line ticker previewing the
 * latest exchange. Right: an expand chevron. Expands UPWARD into a CRT
 * drawer (max 40vh, monitor chrome) carrying the full ARIA chat that the
 * retired floating assistant provided: history, recommendations, system
 * focus toggles, voice input, send, clear.
 *
 * Self-contained (props: none) — reads its data exactly the way the previous
 * ARIA component did, via WebSocketContext (sendARIAMessage / ariaMessages /
 * clearARIAMessages / isConnected) and AuthContext.
 *
 * Security posture ported intact from the previous assistant:
 * - DOMPurify sanitization on all outbound input (XSS prevention)
 * - Input length limits, client-side rate limiting
 * - WebSocket transport only (no ad-hoc HTTP chat calls)
 *
 * Intentionally NOT ported (no-mock-data rule):
 * - the hardcoded "assistant status" API-quota panel (displayed fabricated
 *   quota numbers)
 * - the client-side fabricated recommendation tips fallback (when the real
 *   recommendation endpoints return nothing, the panel now simply stays
 *   empty instead of inventing advice)
 */

import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import DOMPurify from 'dompurify';
import { useAuth } from '../../contexts/AuthContext';
import { useWebSocket } from '../../contexts/WebSocketContext';
import './aria-console-strip.css';

interface AIRecommendation {
  id: string;
  category: 'trading' | 'combat' | 'colony' | 'port' | 'strategic';
  recommendation_type: string;
  title: string;
  summary: string;
  priority: number;
  risk_assessment: 'very_low' | 'low' | 'medium' | 'high' | 'very_high';
  confidence: number;
  expected_outcome: {
    type: string;
    value: number;
    currency?: string;
    probability?: number;
  };
  expires_at: string;
  security_clearance_required: string;
}

const MAX_MESSAGE_LENGTH = 4000;
const MIN_REQUEST_INTERVAL = 1000; // 1 second between requests
const MAX_REQUESTS_PER_MINUTE = 30;

const SYSTEM_OPTIONS: Array<{ key: string; label: string }> = [
  { key: 'trading', label: 'TRADE' },
  { key: 'combat', label: 'COMBAT' },
  { key: 'colony', label: 'COLONY' },
  { key: 'port', label: 'PORT' },
  { key: 'strategic', label: 'STRATEGIC' }
];

const WELCOME_SUGGESTIONS = [
  "What's the best trade route right now?",
  'Help me plan my next strategic move',
  'Should I buy that port in sector 15?',
  'Analyze my combat readiness'
];

const AriaConsoleStrip: React.FC = () => {
  // ── Console state ──────────────────────────────────────────────────
  const [expanded, setExpanded] = useState(false);
  const [unread, setUnread] = useState(false);
  const [inputValue, setInputValue] = useState('');
  const [isThinking, setIsThinking] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [selectedSystems, setSelectedSystems] = useState<string[]>(['trading']);
  const [recommendations, setRecommendations] = useState<AIRecommendation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [isListening, setIsListening] = useState(false);
  const [voiceSupported, setVoiceSupported] = useState(false);

  // Client-side rate limiting (ported intact)
  const [rateLimitWarning, setRateLimitWarning] = useState(false);
  const [lastRequestTime, setLastRequestTime] = useState(0);
  const [requestCount, setRequestCount] = useState(0);

  // ── Refs ───────────────────────────────────────────────────────────
  const logEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const stripBarRef = useRef<HTMLButtonElement>(null);
  const recognitionRef = useRef<any>(null);
  const thinkingTimeoutRef = useRef<number | null>(null);

  // ── Context hooks (same data path as the retired assistant) ───────
  const { user } = useAuth();
  const { sendARIAMessage, ariaMessages, clearARIAMessages, isConnected } = useWebSocket();

  // Same-origin default: the Vite proxy / nginx gateway route /api to the
  // gameserver in every tier (ported from the previous assistant).
  const API_BASE_URL = useMemo(() => {
    if (typeof window !== 'undefined') {
      const protocol = window.location.protocol;
      const hostname = window.location.hostname;
      if (hostname.includes('app.github.dev')) {
        return `${protocol}//${hostname.replace('-3000', '-8080')}`;
      }
      if (hostname.includes('repl.co')) {
        return `${protocol}//${hostname}:8080`;
      }
      return window.location.origin;
    }
    return '';
  }, []);

  // ── Auto-scroll the log while the drawer is open ───────────────────
  useEffect(() => {
    if (expanded) {
      logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [ariaMessages, isThinking, expanded]);

  // ── Thinking + unread bookkeeping on inbound messages ──────────────
  useEffect(() => {
    if (ariaMessages.length === 0) return;
    const last = ariaMessages[ariaMessages.length - 1];
    if (last.type === 'ai') {
      setIsThinking(false);
      if (thinkingTimeoutRef.current !== null) {
        window.clearTimeout(thinkingTimeoutRef.current);
        thinkingTimeoutRef.current = null;
      }
      // New-response indicator on the core when collapsed
      setUnread(prev => prev || !expanded);
    }
    // `expanded` intentionally omitted: this effect marks unread at the
    // moment a message ARRIVES; expanding later clears it (below) without
    // re-running this arrival logic.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ariaMessages]);

  // Clear the timeout on unmount
  useEffect(() => {
    return () => {
      if (thinkingTimeoutRef.current !== null) {
        window.clearTimeout(thinkingTimeoutRef.current);
      }
    };
  }, []);

  // ── Fetch real recommendations when the drawer opens ───────────────
  const fetchRecommendations = useCallback(async () => {
    try {
      const token = localStorage.getItem('accessToken');
      if (!token) {
        setRecommendations([]);
        return;
      }

      const response = await fetch(`${API_BASE_URL}/api/v1/ai/recommendations`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
          system_types: selectedSystems.length > 0 ? selectedSystems : ['trading'],
          max_recommendations: 5
        })
      });

      if (response.ok) {
        const data = await response.json();
        if (Array.isArray(data) && data.length > 0) {
          setRecommendations(data);
          return;
        }
      }

      // If the cross-system endpoint returned empty/failed, try the
      // trading-specific endpoint (ported behavior).
      const tradingResponse = await fetch(`${API_BASE_URL}/ai/recommendations?limit=5`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });

      if (tradingResponse.ok) {
        const tradingData = await tradingResponse.json();
        if (Array.isArray(tradingData) && tradingData.length > 0) {
          const mapped: AIRecommendation[] = tradingData.map((rec: any) => ({
            id: rec.id || String(Math.random()),
            category: 'trading' as const,
            recommendation_type: rec.type || 'trade_opportunity',
            title: rec.reasoning?.substring(0, 60) || 'Trading Opportunity',
            summary: rec.reasoning || 'A trading opportunity has been identified.',
            priority: rec.priority || 3,
            risk_assessment: (rec.risk_level || 'medium') as AIRecommendation['risk_assessment'],
            confidence: rec.confidence || 0.7,
            expected_outcome: {
              type: 'profit',
              value: rec.expected_profit || 0,
              currency: 'credits',
              probability: rec.confidence || 0.7
            },
            expires_at: rec.expires_at || new Date(Date.now() + 3600000).toISOString(),
            security_clearance_required: 'standard'
          }));
          setRecommendations(mapped);
          return;
        }
      }

      // No real recommendations available — show none (no fabricated tips).
      setRecommendations([]);
    } catch (error) {
      console.warn('Failed to fetch AI recommendations:', error);
      setRecommendations([]);
    }
  }, [selectedSystems, API_BASE_URL]);

  useEffect(() => {
    if (user && expanded) {
      fetchRecommendations();
    }
  }, [user, expanded, fetchRecommendations]);

  // ── Speech recognition (ported; no native dialogs) ─────────────────
  useEffect(() => {
    if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
      const SpeechRecognition =
        (window as any).webkitSpeechRecognition || (window as any).SpeechRecognition;
      recognitionRef.current = new SpeechRecognition();
      recognitionRef.current.continuous = false;
      recognitionRef.current.interimResults = false;
      recognitionRef.current.lang = 'en-US';

      recognitionRef.current.onresult = (event: any) => {
        const transcript = event.results[0][0].transcript;
        setInputValue(DOMPurify.sanitize(transcript.trim()));
        setIsListening(false);
      };
      recognitionRef.current.onerror = () => setIsListening(false);
      recognitionRef.current.onend = () => setIsListening(false);
      setVoiceSupported(true);
    }
  }, []);

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

  // ── Send via WebSocket (ported intact) ─────────────────────────────
  const sendMessage = useCallback(() => {
    if (!inputValue.trim() || isThinking || !checkRateLimit() || !isConnected) {
      if (!isConnected) {
        setRateLimitWarning(true);
        setTimeout(() => setRateLimitWarning(false), 3000);
      }
      return;
    }

    const sanitizedMessage = sanitizeInput(inputValue.trim());
    if (!sanitizedMessage) {
      return;
    }

    setIsThinking(true);

    // Context follows the system focus toggles (ported behavior)
    let context = 'general';
    if (selectedSystems.length === 1) {
      context = selectedSystems[0];
    } else if (selectedSystems.includes('trading')) {
      context = 'trading';
    }

    const success = sendARIAMessage(sanitizedMessage, conversationId ?? undefined, context);

    if (success) {
      setInputValue('');
      if (!conversationId) {
        const newConversationId = `conv_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
        setConversationId(newConversationId);
      }
    } else {
      console.error('Failed to send ARIA message via WebSocket');
    }

    // Safety timeout: stop the thinking pulse if no response arrives
    if (thinkingTimeoutRef.current !== null) {
      window.clearTimeout(thinkingTimeoutRef.current);
    }
    thinkingTimeoutRef.current = window.setTimeout(() => setIsThinking(false), 5000);
  }, [inputValue, isThinking, checkRateLimit, sanitizeInput, conversationId, sendARIAMessage, selectedSystems, isConnected]);

  // ── Collapse the drawer and hand keyboard focus back to the strip bar ─
  const collapseDrawer = useCallback(() => {
    setExpanded(false);
    // Return focus to the strip bar so the keyboard user isn't stranded on
    // a now-unmounted input (the drawer JSX is gone once expanded is false).
    window.setTimeout(() => stripBarRef.current?.focus(), 0);
  }, []);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      collapseDrawer();
    }
  }, [sendMessage, collapseDrawer]);

  // ── Voice input (no native alert: button renders only if supported) ─
  const toggleVoiceInput = useCallback(() => {
    if (!recognitionRef.current) return;
    if (isListening) {
      recognitionRef.current.stop();
      setIsListening(false);
    } else {
      recognitionRef.current.start();
      setIsListening(true);
    }
  }, [isListening]);

  const clearConversation = useCallback(() => {
    clearARIAMessages();
    setConversationId(null);
  }, [clearARIAMessages]);

  const acceptRecommendation = useCallback((recommendation: AIRecommendation) => {
    setRecommendations(prev => prev.filter(r => r.id !== recommendation.id));
    const success = sendARIAMessage(
      `I accept your recommendation: ${recommendation.title}`,
      conversationId ?? undefined,
      'trading'
    );
    if (!success) {
      console.error('Failed to send acceptance message to ARIA');
    }
  }, [sendARIAMessage, conversationId]);

  // ── Expand / collapse ──────────────────────────────────────────────
  const toggleExpanded = useCallback(() => {
    setExpanded(prev => {
      const next = !prev;
      if (next) {
        setUnread(false);
        // Focus the input once the drawer paints
        window.setTimeout(() => inputRef.current?.focus(), 50);
      }
      return next;
    });
  }, []);

  // ── Ticker line (latest exchange preview) ──────────────────────────
  const lastMessage = ariaMessages.length > 0 ? ariaMessages[ariaMessages.length - 1] : null;
  // While processing, the ticker reads "PROCESSING…" — that line is ARIA
  // speaking, so the prefix must be ARIA> even when the last logged message
  // was the player's own query (otherwise it reads "YOU> PROCESSING…").
  const tickerPrefix = isThinking
    ? 'ARIA>'
    : lastMessage
      ? (lastMessage.type === 'ai' ? 'ARIA>' : 'YOU>')
      : 'ARIA>';
  const tickerText = isThinking
    ? 'PROCESSING…'
    : lastMessage
      ? lastMessage.content
      : isConnected
        ? 'SHIP INTELLIGENCE ONLINE — STANDING BY'
        : 'UPLINK OFFLINE — AWAITING CONNECTION';

  const coreStateClass = isThinking ? 'thinking' : isConnected ? 'idle' : 'offline';

  return (
    <div className="aria-console-strip">
      {/* ── CRT drawer (expands upward) ── */}
      {expanded && (
        <section
          className="aria-drawer"
          aria-label="ARIA ship intelligence console"
          onKeyDown={(e) => {
            // Escape from anywhere in the drawer collapses it (the input has
            // its own handler too; this catches focus on the control buttons).
            if (e.key === 'Escape') {
              e.preventDefault();
              collapseDrawer();
            }
          }}
        >
          <span className="aria-bezel-corner tl" aria-hidden="true" />
          <span className="aria-bezel-corner tr" aria-hidden="true" />

          <header className="aria-drawer-header">
            <span className={`aria-link-led ${isConnected ? 'online' : 'offline'}`} aria-hidden="true" />
            <h2 className="aria-drawer-title">ARIA — SHIP INTELLIGENCE</h2>
            <span className="aria-link-status">{isConnected ? 'LINK OK' : 'LINK DOWN'}</span>
            <div className="aria-drawer-controls">
              <button
                type="button"
                className={`aria-ctl-btn ${showSettings ? 'active' : ''}`}
                onClick={() => setShowSettings(s => !s)}
                aria-pressed={showSettings}
                aria-label="Toggle system focus settings"
              >
                SYS
              </button>
              {ariaMessages.length > 0 && (
                <button
                  type="button"
                  className="aria-ctl-btn"
                  onClick={clearConversation}
                  aria-label="Clear conversation"
                >
                  CLEAR
                </button>
              )}
              <button
                type="button"
                className="aria-ctl-btn"
                onClick={toggleExpanded}
                aria-label="Collapse ARIA console"
              >
                ▾
              </button>
            </div>
          </header>

          {/* ── Middle region — the ONE scroll body (Law 2 inside the drawer).
              Header stays pinned above; input row + char-count footer stay
              pinned below. Settings + advisories + log all live here and
              scroll together, so with SYS open + 3 advisories the XMIT row
              and footer can never be clipped (see .aria-drawer-body arith). */}
          <div className="aria-drawer-body">
          {showSettings && (
            <div className="aria-settings">
              <span className="aria-settings-label">SYSTEM FOCUS</span>
              <div className="aria-system-toggles">
                {SYSTEM_OPTIONS.map(({ key, label }) => (
                  <label key={key} className={`aria-system-toggle ${selectedSystems.includes(key) ? 'on' : ''}`}>
                    <input
                      type="checkbox"
                      checked={selectedSystems.includes(key)}
                      onChange={(e) => {
                        if (e.target.checked) {
                          setSelectedSystems(prev => [...prev, key]);
                        } else {
                          setSelectedSystems(prev => prev.filter(s => s !== key));
                        }
                      }}
                    />
                    <span>{label}</span>
                  </label>
                ))}
              </div>
            </div>
          )}

          {recommendations.length > 0 && (
            <div className="aria-recommendations">
              <span className="aria-recs-label">ADVISORIES</span>
              {recommendations.slice(0, 3).map((rec) => (
                <div key={rec.id} className="aria-rec-card">
                  <div className="aria-rec-head">
                    <span className="aria-rec-title">{rec.title}</span>
                    <span className={`aria-rec-risk risk-${rec.risk_assessment}`}>
                      {rec.risk_assessment.replace('_', ' ').toUpperCase()}
                    </span>
                  </div>
                  <div className="aria-rec-summary">{rec.summary}</div>
                  <div className="aria-rec-foot">
                    {rec.expected_outcome.type === 'profit' && (
                      <span className="aria-rec-outcome">
                        EST {new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(rec.expected_outcome.value)} CR
                      </span>
                    )}
                    <span className="aria-rec-confidence">{Math.round(rec.confidence * 100)}% CONF</span>
                    <button
                      type="button"
                      className="aria-ctl-btn accent"
                      onClick={() => acceptRecommendation(rec)}
                    >
                      ACCEPT
                    </button>
                    <button
                      type="button"
                      className="aria-ctl-btn"
                      onClick={() => setInputValue(`Tell me more about: ${rec.title}`)}
                    >
                      DETAIL
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="aria-log" role="log" aria-live="polite">
            {ariaMessages.length === 0 && (
              <div className="aria-welcome">
                <div className="aria-log-line ai">
                  <span className="aria-log-prefix">ARIA&gt;</span>
                  <span className="aria-log-text">
                    Ship intelligence online. Strategic trading, combat tactics, colonization
                    planning — state your query, Commander.
                  </span>
                </div>
                <div className="aria-suggestions">
                  {WELCOME_SUGGESTIONS.map((suggestion, idx) => (
                    <button
                      key={idx}
                      type="button"
                      className="aria-suggestion-chip"
                      onClick={() => setInputValue(suggestion)}
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {ariaMessages.map((message) => (
              <div key={message.id} className={`aria-log-line ${message.type}`}>
                <span className="aria-log-prefix">{message.type === 'ai' ? 'ARIA>' : 'YOU>'}</span>
                <span className="aria-log-text">
                  {message.content}
                  {message.type === 'ai' && typeof message.confidence === 'number' && (
                    <span className="aria-log-meta"> [{Math.round(message.confidence * 100)}%]</span>
                  )}
                </span>
                {message.type === 'ai' && message.suggestions && message.suggestions.length > 0 && (
                  <div className="aria-suggestions inline">
                    {message.suggestions.slice(0, 3).map((suggestion, idx) => (
                      <button
                        key={idx}
                        type="button"
                        className="aria-suggestion-chip"
                        onClick={() => setInputValue(suggestion)}
                      >
                        {suggestion}
                      </button>
                    ))}
                  </div>
                )}
                {message.type === 'ai' && message.actions && message.actions.length > 0 && (
                  <div className="aria-log-actions">
                    {message.actions.map((action, idx) => (
                      <div key={idx} className="aria-log-action">
                        <span className="aria-log-action-type">{action.type}:</span>{' '}
                        {JSON.stringify(action)}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}

            {isThinking && (
              <div className="aria-log-line ai thinking">
                <span className="aria-log-prefix">ARIA&gt;</span>
                <span className="aria-log-text aria-thinking-text">PROCESSING<span className="aria-ellipsis" /></span>
              </div>
            )}

            <div ref={logEndRef} />
          </div>
          </div>
          {/* ── /aria-drawer-body — input row + footer below are pinned ── */}

          {rateLimitWarning && (
            <div className="aria-rate-warning" role="status">
              ⚠ {isConnected ? 'TRANSMISSION RATE EXCEEDED — STAND BY' : 'UPLINK OFFLINE — MESSAGE NOT SENT'}
            </div>
          )}

          <div className="aria-input-row">
            <span className="aria-input-prompt" aria-hidden="true">&gt;</span>
            <input
              ref={inputRef}
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="QUERY SHIP INTELLIGENCE…"
              maxLength={MAX_MESSAGE_LENGTH}
              className="aria-input"
              disabled={isThinking}
              aria-label="Message ARIA"
            />
            {voiceSupported && (
              <button
                type="button"
                onClick={toggleVoiceInput}
                className={`aria-ctl-btn ${isListening ? 'listening' : ''}`}
                aria-label={isListening ? 'Stop listening' : 'Start voice input'}
                disabled={isThinking}
              >
                VOX
              </button>
            )}
            <button
              type="button"
              onClick={sendMessage}
              className="aria-ctl-btn accent xmit"
              disabled={!inputValue.trim() || isThinking}
              aria-label="Send message"
            >
              XMIT
            </button>
          </div>
          <div className="aria-input-footer">
            <span className="aria-char-count">{inputValue.length}/{MAX_MESSAGE_LENGTH}</span>
          </div>
        </section>
      )}

      {/* ── The 36px console strip ── */}
      <button
        ref={stripBarRef}
        type="button"
        className="aria-strip-bar"
        onClick={toggleExpanded}
        aria-expanded={expanded}
        aria-label={expanded ? 'Collapse ARIA console' : 'Expand ARIA console'}
      >
        <span className={`aria-core ${coreStateClass} ${unread && !expanded ? 'unread' : ''}`} aria-hidden="true">
          <span className="aria-core-ring" />
          <span className="aria-core-dot" />
        </span>
        <span className="aria-strip-label" aria-hidden="true">ARIA</span>
        <span className="aria-ticker">
          <span className={`aria-ticker-prefix ${!isThinking && lastMessage?.type === 'user' ? 'user' : 'ai'}`}>
            {tickerPrefix}
          </span>{' '}
          <span className="aria-ticker-text">{tickerText}</span>
        </span>
        {unread && !expanded && <span className="aria-unread-tag">NEW</span>}
        <span className="aria-strip-chevron" aria-hidden="true">{expanded ? '▾' : '▴'}</span>
      </button>
    </div>
  );
};

export default AriaConsoleStrip;
