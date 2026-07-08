import React, { useState, useEffect } from 'react';
import { useFirstLogin } from '../../contexts/FirstLoginContext';
import ShipSelection from './ShipSelection';
import DialogueExchange from './DialogueExchange';
import OutcomeDisplay from './OutcomeDisplay';
import './first-login.css';

// Guard identity is now sourced from the server (persisted guard_* columns,
// src/utils/guard_personalities.py) instead of a client-side hash mirror —
// WO-PUX-FLOGIN-RESUME retired src/utils/guardPersonalities.ts.
interface GuardPersonality {
  name: string;
  title: string;
  trait: string;
  baseSuspicion: number;
  description: string;
}

/**
 * FirstLoginContainer - Interrogation Booth UI
 *
 * The main component for the first login experience, redesigned as an immersive
 * interrogation booth with guard profile, central trust meter, and ship claim display.
 *
 * Layout (3-column grid during dialogue):
 * - Left: Guard Profile Panel (silhouette, personality, stats)
 * - Center: Trust Meter (top) + Dialogue Exchange (bottom)
 * - Right: Ship Claim Display (holographic ship, specs, ownership status)
 */
const FirstLoginContainer: React.FC = () => {
  const {
    isLoading,
    error,
    session,
    startSession,
    resetError,
    resetSession,
    requiresFirstLogin,
    dialogueOutcome,
    dialogueHistory
  } = useFirstLogin();

  // Track which step of the first login experience we're on
  const [currentStep, setCurrentStep] = useState<'ship_selection' | 'dialogue' | 'completion'>(
    'ship_selection'
  );

  // Guard personality (generated once per session)
  const [guardPersonality, setGuardPersonality] = useState<GuardPersonality | null>(null);

  // Trust level (0-1) that updates with each response
  const [currentTrust, setCurrentTrust] = useState<number>(0.5);

  // Trust level classification for color coding
  const getTrustLevel = (trust: number): 'high' | 'medium' | 'warning' | 'danger' => {
    if (trust >= 0.8) return 'high';
    if (trust >= 0.6) return 'medium';
    if (trust >= 0.4) return 'warning';
    return 'danger';
  };

  // Update trust level based on dialogue history
  useEffect(() => {
    if (dialogueHistory && dialogueHistory.length > 1) {
      // Get the latest exchange with scores
      const latestExchange = dialogueHistory
        .filter(ex => ex.player && (
          ex.consistency !== null && ex.consistency !== undefined &&
          ex.confidence !== null && ex.confidence !== undefined &&
          ex.persuasiveness !== null && ex.persuasiveness !== undefined
        ))
        .pop();

      if (latestExchange) {
        // Calculate trust based on consistency, confidence, and persuasiveness
        // Using the same 50/30/20 weighting as backend
        const consistency = latestExchange.consistency!;
        const confidence = latestExchange.confidence!;
        const persuasiveness = latestExchange.persuasiveness!;

        const calculatedTrust = (
          consistency * 0.5 +
          confidence * 0.3 +
          persuasiveness * 0.2
        );

        setCurrentTrust(calculatedTrust);
      }
    } else if (guardPersonality) {
      // Initial trust = inverted base suspicion
      setCurrentTrust(1 - guardPersonality.baseSuspicion);
    }
  }, [dialogueHistory, guardPersonality]);

  // Initialize the first login session when the component mounts
  useEffect(() => {
    if (requiresFirstLogin && !session && !isLoading) {
      startSession();
    }

    // Update the current step based on the session state
    if (session) {
      setCurrentStep(session.current_step);

      // Guard personality is persisted server-side per session — read it
      // straight off the session response instead of re-deriving it.
      if (!guardPersonality && session.guard_name) {
        const guard: GuardPersonality = {
          name: session.guard_name,
          title: session.guard_title || '',
          trait: session.guard_trait || '',
          baseSuspicion: session.guard_base_suspicion ?? 0.5,
          description: session.guard_description || '',
        };
        setGuardPersonality(guard);
        // Set initial trust based on guard's base suspicion (inverted)
        setCurrentTrust(1 - guard.baseSuspicion);
      }
    }
  }, [requiresFirstLogin, session, isLoading]); // Removed guardPersonality to prevent infinite loop

  // If the player doesn't need first login, don't show this component
  if (!requiresFirstLogin) {
    return null;
  }

  // Render guard silhouette SVG
  const renderGuardSilhouette = () => (
    <div className="guard-silhouette">
      <svg viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg">
        {/* Simple humanoid silhouette */}
        <ellipse cx="100" cy="60" rx="35" ry="40" className="guard-silhouette-shape" />
        <rect x="70" y="95" width="60" height="80" rx="10" className="guard-silhouette-shape" />
        <rect x="45" y="100" width="25" height="60" rx="8" className="guard-silhouette-shape" />
        <rect x="130" y="100" width="25" height="60" rx="8" className="guard-silhouette-shape" />
      </svg>
      <div className="scanning-lines"></div>
    </div>
  );

  // Render guard profile panel (left column)
  const renderGuardProfile = () => {
    if (!guardPersonality) return null;

    return (
      <div className="guard-profile-panel">
        {renderGuardSilhouette()}

        <div className="guard-info">
          <div className="guard-name">{guardPersonality.name}</div>
          <div className="guard-title">{guardPersonality.title}</div>
          <div className="guard-trait">"{guardPersonality.trait}"</div>

          <div className="guard-stats">
            <div className="guard-stat">
              <span className="guard-stat-label">PERSONALITY</span>
              <span className="guard-stat-value">{guardPersonality.description}</span>
            </div>
            <div className="guard-stat">
              <span className="guard-stat-label">BASE SUSPICION</span>
              <span className="guard-stat-value">{(guardPersonality.baseSuspicion * 100).toFixed(0)}%</span>
            </div>
            <div className="suspicion-meter">
              <div
                className="suspicion-fill"
                style={{ width: `${guardPersonality.baseSuspicion * 100}%` }}
              ></div>
            </div>
          </div>
        </div>
      </div>
    );
  };

  // Render central trust meter
  const renderTrustMeter = () => {
    const trustPercent = Math.round(currentTrust * 100);
    const trustLevel = getTrustLevel(currentTrust);

    // SVG circle parameters
    const radius = 94;
    const circumference = 2 * Math.PI * radius;
    const strokeDashoffset = circumference - (currentTrust * circumference);

    // Pinned to the marker's actual lifelong behavior: the server never
    // serializes ship_choice (only ship_claimed), so this expression always
    // evaluated 0.5 and the 0.6 branch never executed. Whether the marker
    // SHOULD respond to ship_claimed (and at what threshold) is an open
    // product call -- escalated in STATUS; wire ship_claimed here only
    // after that ruling.
    const thresholdPercent = 0.5;
    const thresholdRotation = (thresholdPercent * 360) - 90;

    return (
      <div className="trust-meter-section">
        <div className="trust-meter-title">TRUST ASSESSMENT</div>

        <div className="trust-meter-container">
          <div className="trust-meter-circle">
            <svg>
              <circle
                className="trust-meter-bg"
                cx="100"
                cy="100"
                r={radius}
              />
              <circle
                className={`trust-meter-progress ${trustLevel}`}
                cx="100"
                cy="100"
                r={radius}
                strokeDasharray={circumference}
                strokeDashoffset={strokeDashoffset}
              />
            </svg>

            <div className="trust-meter-center">
              <div className={`trust-meter-value ${trustLevel}`}>{trustPercent}</div>
              <div className="trust-meter-label">TRUST</div>
            </div>

            {/* Threshold marker */}
            <div className="threshold-marker">
              <div
                className="threshold-indicator"
                style={{ transform: `rotate(${thresholdRotation}deg)` }}
              ></div>
            </div>
          </div>
        </div>

        {/* Risk assessment warning */}
        <div className={`risk-assessment ${trustLevel === 'high' ? 'safe' : trustLevel === 'medium' ? 'caution' : 'danger'}`}>
          {trustLevel === 'high' && '✓ VERIFICATION LIKELY'}
          {trustLevel === 'medium' && '⚠ APPROACHING THRESHOLD'}
          {trustLevel === 'warning' && '⚠ CRITICAL - BELOW THRESHOLD'}
          {trustLevel === 'danger' && '✗ FAILURE IMMINENT'}
        </div>

        {/* Score breakdown */}
        {dialogueHistory && dialogueHistory.length > 0 && (() => {
          const latestExchange = dialogueHistory
            .filter(ex => ex.player && (
              ex.consistency !== null && ex.consistency !== undefined &&
              ex.confidence !== null && ex.confidence !== undefined &&
              ex.persuasiveness !== null && ex.persuasiveness !== undefined
            ))
            .pop();

          if (!latestExchange) return null;

          return (
            <div className="score-breakdown">
              <div className="score-item">
                <span className="score-item-label">CONSISTENCY</span>
                <span className="score-item-value">
                  {latestExchange.consistency !== null ? (latestExchange.consistency * 100).toFixed(0) : '-'}%
                </span>
              </div>
              <div className="score-item">
                <span className="score-item-label">CONFIDENCE</span>
                <span className="score-item-value">
                  {latestExchange.confidence !== null ? (latestExchange.confidence * 100).toFixed(0) : '-'}%
                </span>
              </div>
              <div className="score-item">
                <span className="score-item-label">PERSUASIVE</span>
                <span className="score-item-value">
                  {latestExchange.persuasiveness !== null ? (latestExchange.persuasiveness * 100).toFixed(0) : '-'}%
                </span>
              </div>
            </div>
          );
        })()}
      </div>
    );
  };

  // Render ship claim panel (right column)
  const renderShipClaim = () => {
    if (!session?.ship_claimed) return null;

    const shipName = session.ship_claimed.replace(/_/g, ' ');
    const shipClass = session.ship_claimed.toLowerCase().replace(/_/g, '-');

    return (
      <div className="ship-claim-panel">
        <div className="ship-claim-title">CLAIM VERIFICATION</div>

        {/* Holographic ship display */}
        <div className="ship-hologram">
          <div
            className={`ship-hologram-image ship-image ${shipClass}`}
            style={{ backgroundImage: `url('/ships/${shipClass}.png')` }}
          ></div>
        </div>

        {/* Ship specifications */}
        <div className="ship-specs">
          <div className="ship-spec-item">
            <span className="ship-spec-label">CLASS</span>
            <span className="ship-spec-value">{shipName}</span>
          </div>
          <div className="ship-spec-item">
            <span className="ship-spec-label">TIER</span>
            <span className="ship-spec-value">
              {session.ship_claimed === 'ESCAPE_POD' ? 'I' :
               session.ship_claimed === 'LIGHT_FREIGHTER' ? 'II' :
               session.ship_claimed === 'SCOUT_SHIP' || session.ship_claimed === 'FAST_COURIER' ? 'III' :
               session.ship_claimed === 'CARGO_HAULER' ? 'IV' :
               session.ship_claimed === 'DEFENDER' ? 'V' : 'VI+'}
            </span>
          </div>
          <div className="ship-spec-item">
            <span className="ship-spec-label">VALUE</span>
            <span className="ship-spec-value">
              {session.ship_claimed === 'ESCAPE_POD' ? '5K CR' :
               session.ship_claimed === 'LIGHT_FREIGHTER' ? '150K CR' :
               session.ship_claimed === 'SCOUT_SHIP' ? '500K CR' :
               session.ship_claimed === 'FAST_COURIER' ? '450K CR' :
               session.ship_claimed === 'CARGO_HAULER' ? '1.2M CR' :
               session.ship_claimed === 'DEFENDER' ? '2.5M CR' : '5M+ CR'}
            </span>
          </div>
        </div>

        {/* Ownership status */}
        <div className="ownership-status">
          <div className="ownership-status-label">STATUS</div>
          <div className="ownership-status-value">UNVERIFIED</div>
        </div>
      </div>
    );
  };

  return (
    <div className="first-login-container">
      <div className="dialogue-box">
        {/* Header with game title */}
        <div className="game-title-header">
          <h1 className="game-title">SECTOR WARS 2102</h1>
          <p className="game-subtitle">Security Checkpoint - Callisto Colony</p>
          <p className="location-context">Docking Bay 7 - Authorization Required</p>

          {/* Dev Reset Button - only visible in development mode */}
          {import.meta.env.DEV && session && (
            <button
              className="dev-reset-button"
              onClick={() => {
                if (window.confirm('Reset first login session? This will clear your progress.')) {
                  resetSession();
                  window.location.reload();
                }
              }}
              title="Reset first login session (dev only)"
            >
              🔄 Reset
            </button>
          )}
        </div>

        {/* Loading state */}
        {isLoading && (
          <div className="loading-message">
            <div className="loading-spinner"></div>
            <p>Initializing security protocols...</p>
          </div>
        )}

        {/* Error state */}
        {error && (
          <div className="error-message">
            <p>{error}</p>
            <button onClick={resetError}>Try Again</button>
          </div>
        )}

        {/* Waiting for session start */}
        {!isLoading && !error && !session && (
          <div className="waiting-message">
            <p>Preparing your arrival at the spaceport...</p>
            <button onClick={startSession}>Begin Registration</button>
          </div>
        )}

        {/* Ship Selection Phase (fullscreen, no columns) */}
        {currentStep === 'ship_selection' && session && (
          <ShipSelection />
        )}

        {/* Dialogue Phase (3-column interrogation booth layout) */}
        {currentStep === 'dialogue' && session && guardPersonality && (
          <div className="interrogation-booth">
            {renderGuardProfile()}

            <div className="center-panel">
              {renderTrustMeter()}
              <div className="dialogue-exchange-section">
                <DialogueExchange />
              </div>
            </div>

            {renderShipClaim()}
          </div>
        )}

        {/* Completion Phase (outcome display) */}
        {(currentStep === 'completion' || dialogueOutcome) && (
          <OutcomeDisplay />
        )}
      </div>
    </div>
  );
};

export default FirstLoginContainer;
