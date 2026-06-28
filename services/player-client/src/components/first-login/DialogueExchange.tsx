import React, { useState, useEffect, useRef, ReactNode } from 'react';
import { useFirstLogin } from '../../contexts/FirstLoginContext';
import './first-login.css';

// Convert *text* to <em>text</em> for action markers
const formatDialogue = (text: string): ReactNode[] => {
  const parts = text.split(/(\*[^*]+\*)/g);
  return parts.map((part, i) =>
    part.startsWith('*') && part.endsWith('*')
      ? <em key={i} style={{ color: '#888', fontStyle: 'italic' }}>{part.slice(1, -1)}</em>
      : part
  );
};

/**
 * DialogueExchange component - Chat-style conversation interface
 *
 * Handles the ongoing conversation between the player and the security guard
 * with modern chat UI, typing indicators, and score badges.
 */
const DialogueExchange: React.FC = () => {
  const {
    currentPrompt,
    dialogueHistory,
    submitResponse,
    isLoading,
    dialogueOutcome,
    session
  } = useFirstLogin();

  const [response, setResponse] = useState('');
  const [showTypingIndicator, setShowTypingIndicator] = useState(false);
  const dialogueHistoryRef = useRef<HTMLDivElement>(null);

  // Show typing indicator when loading
  useEffect(() => {
    if (isLoading) {
      setShowTypingIndicator(true);
    } else {
      // Delay hiding typing indicator for smooth transition
      const timeout = setTimeout(() => setShowTypingIndicator(false), 300);
      return () => clearTimeout(timeout);
    }
  }, [isLoading]);

  // Auto-scroll to the bottom of the dialogue history when it updates
  useEffect(() => {
    if (dialogueHistoryRef.current) {
      dialogueHistoryRef.current.scrollTop = dialogueHistoryRef.current.scrollHeight;
    }
  }, [dialogueHistory, showTypingIndicator]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (response.trim() && !isLoading && !dialogueOutcome) {
      try {
        await submitResponse(response);
        setResponse('');
      } catch (error) {
        console.error('Error submitting response:', error);
      }
    }
  };

  // Get score badge class based on score value
  const getScoreBadgeClass = (score: number | null): string => {
    if (score === null) return 'medium';
    if (score >= 0.7) return 'high';
    if (score >= 0.4) return 'medium';
    return 'low';
  };

  // Render typing indicator
  const renderTypingIndicator = () => (
    <div className="typing-indicator">
      <span className="typing-indicator-text">Security guard is thinking</span>
      <div className="typing-dots">
        <div className="typing-dot"></div>
        <div className="typing-dot"></div>
        <div className="typing-dot"></div>
      </div>
    </div>
  );

  return (
    <>
      {/* Dialogue history (scrollable chat area) */}
      <div className="dialogue-history" ref={dialogueHistoryRef}>
        {dialogueHistory && dialogueHistory.length > 0 ? (
          <>
            {dialogueHistory.map((exchange, index) => {
              const isLastExchange = index === dialogueHistory.length - 1;

              return (
                <div key={index} className="history-item">
                  {/* Guard's message */}
                  {exchange.npc && (
                    <div className="npc-message">
                      <div className="message-meta">
                        <span>Security Guard</span>
                        {/* Debug indicator */}
                        {exchange.npc.includes('[RULE-BASED]') && (
                          <span className="debug-indicator debug-fallback">FALLBACK</span>
                        )}
                        {exchange.npc.includes('[AI-ANTHROPIC]') && (
                          <span className="debug-indicator debug-ai-anthropic">AI-CLAUDE</span>
                        )}
                        {exchange.npc.includes('[AI-OPENAI]') && (
                          <span className="debug-indicator debug-ai-openai">AI-GPT</span>
                        )}
                      </div>
                      <div className="message-text">
                        {formatDialogue(exchange.npc.replace(/\[(RULE-BASED|AI-ANTHROPIC|AI-OPENAI)\]\s*/, ''))}
                      </div>
                    </div>
                  )}

                  {/* Player's message with score badges */}
                  {exchange.player && (
                    <div className="player-message">
                      <div className="message-meta">
                        <span>You</span>
                        {/* Score badges */}
                        {exchange.consistency !== null && exchange.consistency !== undefined && (
                          <span className={`score-badge ${getScoreBadgeClass(exchange.consistency)}`}>
                            C: {(exchange.consistency * 100).toFixed(0)}%
                          </span>
                        )}
                        {exchange.confidence !== null && exchange.confidence !== undefined && (
                          <span className={`score-badge ${getScoreBadgeClass(exchange.confidence)}`}>
                            Conf: {(exchange.confidence * 100).toFixed(0)}%
                          </span>
                        )}
                        {exchange.persuasiveness !== null && exchange.persuasiveness !== undefined && (
                          <span className={`score-badge ${getScoreBadgeClass(exchange.persuasiveness)}`}>
                            P: {(exchange.persuasiveness * 100).toFixed(0)}%
                          </span>
                        )}
                      </div>
                      <div className="message-text">{exchange.player}</div>
                    </div>
                  )}

                  {/* Typing indicator AFTER the last player message */}
                  {isLastExchange && exchange.player && showTypingIndicator && (
                    renderTypingIndicator()
                  )}
                </div>
              );
            })}
          </>
        ) : (
          <div className="loading-message">
            <p>Waiting for guard to begin questioning...</p>
          </div>
        )}
      </div>

      {/* Input area (fixed at bottom of dialogue section) */}
      {!dialogueOutcome && (
        <form onSubmit={handleSubmit} className="dialogue-input-area">
          <textarea
            className="response-input"
            placeholder="Type your response to the guard..."
            value={response}
            onChange={(e) => setResponse(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                if (response.trim() && !isLoading) {
                  handleSubmit(e as unknown as React.FormEvent);
                }
              }
            }}
            disabled={isLoading || !!dialogueOutcome}
            rows={3}
          />

          <div className="response-buttons">
            <button
              type="submit"
              className="submit-response"
              disabled={!response.trim() || isLoading || !!dialogueOutcome}
              title={
                !response.trim()
                  ? 'Type a response first'
                  : isLoading
                    ? 'Sending...'
                    : 'Submit response (Enter)'
              }
            >
              {isLoading ? (
                <>
                  <span className="submit-spinner" aria-hidden="true"></span>
                  Sending...
                </>
              ) : (
                'Submit Response'
              )}
            </button>
          </div>
        </form>
      )}
    </>
  );
};

export default DialogueExchange;
