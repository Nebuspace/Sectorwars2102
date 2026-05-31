import React, { useState } from 'react';
import { useFirstLogin } from '../../contexts/FirstLoginContext';
import { useGame } from '../../contexts/GameContext';
import { useNavigate } from 'react-router-dom';
import './first-login.css';

// Ship display names
const SHIP_NAMES: Record<string, string> = {
  SCOUT_SHIP: "Scout Ship",
  CARGO_FREIGHTER: "Cargo Freighter",
  ESCAPE_POD: "Escape Pod",
  LIGHT_FREIGHTER: "Light Freighter",
  DEFENDER: "Defender",
  FAST_COURIER: "Fast Courier"
};

/**
 * OutcomeDisplay shows the final result of the first login experience,
 * including the awarded ship, credits, and any bonuses or penalties.
 */
const OutcomeDisplay: React.FC = () => {
  const {
    dialogueOutcome,
    completeFirstLogin,
    isLoading
  } = useFirstLogin();

  const { onFirstLoginComplete } = useGame();
  const navigate = useNavigate();
  const [isCompleting, setIsCompleting] = useState(false);
  const [completionResult, setCompletionResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  if (!dialogueOutcome) {
    return null;
  }

  const handleStartGame = async () => {
    setIsCompleting(true);
    setError(null);

    try {
      const result = await completeFirstLogin();
      setCompletionResult(result);

      // Refresh all game data in GameContext
      await onFirstLoginComplete();

      // Redirect to the game dashboard after a short delay
      setTimeout(() => {
        navigate('/game');
      }, 1500);
    } catch (err) {
      console.error('Failed to complete first login:', err);
      setError('Failed to complete registration. Please try again.');
      setIsCompleting(false);
    }
  };

  // Helper function to get outcome message based on outcome type
  const getOutcomeMessage = () => {
    switch (dialogueOutcome.outcome) {
      case 'SUCCESS':
        return "Authentication successful. Welcome aboard, captain!";
      case 'PARTIAL_SUCCESS':
        return "Your story has inconsistencies, but you're cleared to proceed.";
      case 'FAILURE':
        return "Your story doesn't check out. You're limited to basic resources.";
      default:
        return "Processing complete. You may now enter the sector.";
    }
  };

  // Extract AI-generated response (remove debug tags)
  const guardFinalMessage = dialogueOutcome.guard_response
    ? dialogueOutcome.guard_response.replace(/\[(RULE-BASED|AI-ANTHROPIC|AI-OPENAI)\]\s*/, '')
    : getOutcomeMessage();

  // Get outcome header class for styling
  const outcomeHeaderClass = dialogueOutcome.outcome === 'SUCCESS' ? 'success' : 'failure';

  // awarded_ship is only set on SUCCESS / PARTIAL_SUCCESS outcomes; on
  // FAILURE the player gets the default Escape Pod (per FIRST_LOGIN.md and
  // the gameserver's create_player_for_user starter-ship code). Fall back
  // to ESCAPE_POD so the page renders instead of crashing on .toLowerCase().
  const awardedShip = awardedShip ?? 'ESCAPE_POD';

  return (
    <div className="outcome-container">
      {/* Scrollable content area */}
      <div className="outcome-content">
        <h2 className={`outcome-header ${outcomeHeaderClass}`}>
          {dialogueOutcome.outcome === 'SUCCESS' ? 'ACCESS GRANTED' : 'ACCESS DENIED'}
        </h2>

        {/* Guard's final verdict - AI generated */}
        <div className="guard-final-message" style={{
          margin: '20px auto',
          padding: '20px',
          maxWidth: '600px',
          background: 'rgba(74, 144, 226, 0.1)',
          borderLeft: '4px solid #4a90e2',
          borderRadius: '8px',
          textAlign: 'left'
        }}>
          <div className="message-meta" style={{marginBottom: '10px', fontSize: '0.85rem', color: '#888'}}>
            <span>Security Guard</span>
            {/* Debug indicator for final response */}
            {dialogueOutcome.guard_response && dialogueOutcome.guard_response.includes('[RULE-BASED]') && (
              <span className="debug-indicator debug-fallback">FALLBACK</span>
            )}
            {dialogueOutcome.guard_response && dialogueOutcome.guard_response.includes('[AI-ANTHROPIC]') && (
              <span className="debug-indicator debug-ai-anthropic">AI-CLAUDE</span>
            )}
            {dialogueOutcome.guard_response && dialogueOutcome.guard_response.includes('[AI-OPENAI]') && (
              <span className="debug-indicator debug-ai-openai">AI-GPT</span>
            )}
          </div>
          <div className="message-text" style={{fontSize: '1rem', lineHeight: '1.6', color: '#e0e0e0'}}>
            {guardFinalMessage}
          </div>
        </div>

        <div className="outcome-ship">
          <div className={`ship-image-large ${awardedShip.toLowerCase().replace(/_/g, '-')}`}>
            <div className="fallback">{SHIP_NAMES[awardedShip] || awardedShip}</div>
          </div>
          <div className="ship-name">{SHIP_NAMES[awardedShip] || awardedShip}</div>
        </div>

        {/* Score Breakdown - shows why player passed/failed */}
        {dialogueOutcome.final_persuasion_score !== undefined && (
          <div className="score-breakdown" style={{
            margin: '20px 0',
            padding: '15px',
            background: dialogueOutcome.outcome === 'SUCCESS' ? 'rgba(0, 200, 100, 0.1)' : 'rgba(200, 100, 0, 0.1)',
            borderRadius: '8px',
            border: dialogueOutcome.outcome === 'SUCCESS' ? '1px solid rgba(0, 200, 100, 0.3)' : '1px solid rgba(200, 100, 0, 0.3)'
          }}>
            <div style={{fontWeight: 'bold', marginBottom: '10px', color: '#aaa'}}>
              Evaluation Results:
            </div>
            <div style={{fontSize: '0.9em', lineHeight: '1.6'}}>
              <div>Your Persuasion Score: <strong>{dialogueOutcome.final_persuasion_score.toFixed(4)}</strong></div>
              <div>Negotiation Level: <strong>{dialogueOutcome.negotiation_skill}</strong></div>
              <div style={{marginTop: '8px', paddingTop: '8px', borderTop: '1px solid rgba(255, 255, 255, 0.1)'}}>
                {dialogueOutcome.outcome === 'SUCCESS' ? (
                  <span style={{color: '#0c0'}}>
                    ✓ Your score met the threshold for {SHIP_NAMES[awardedShip] || awardedShip}
                  </span>
                ) : (
                  <span style={{color: '#c80'}}>
                    ✗ Your score didn't meet the required threshold. Keep practicing your negotiation skills!
                  </span>
                )}
              </div>
            </div>
          </div>
        )}

        <div className="outcome-details">
          <div className="outcome-item">
            <div className="outcome-icon">💰</div>
            <div className="outcome-value">{dialogueOutcome.starting_credits}</div>
            <div className="outcome-label">Credits</div>
          </div>

          <div className="outcome-item">
            <div className="outcome-icon">🔍</div>
            <div className="outcome-value">{dialogueOutcome.negotiation_skill}</div>
            <div className="outcome-label">Negotiation Skill</div>
          </div>

          {dialogueOutcome.negotiation_bonus && (
            <div className="outcome-item">
              <div className="outcome-icon">⭐</div>
              <div className="outcome-value">Trade Bonus</div>
              <div className="outcome-label">Special Ability</div>
            </div>
          )}

          {dialogueOutcome.notoriety_penalty && (
            <div className="outcome-item">
              <div className="outcome-icon">⚠️</div>
              <div className="outcome-value">Notoriety</div>
              <div className="outcome-label">Reputation Penalty</div>
            </div>
          )}
        </div>
      </div>

      {/* Fixed bottom action area - always visible */}
      <div className="outcome-action-bar">
        {error && <div className="error-message">{error}</div>}

        <button
          className="outcome-start-button"
          onClick={handleStartGame}
          disabled={isLoading || isCompleting}
        >
          {isCompleting ? 'Initializing...' : 'Begin Your Journey'}
        </button>
      </div>
    </div>
  );
};

export default OutcomeDisplay;