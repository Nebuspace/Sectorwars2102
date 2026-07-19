import React from 'react';
import { useFirstSession, FirstSessionProgress } from './useFirstSession';
import './firstSessionObjectives.css';

// WO-PUX-ONBOARD: the dismissible first-session objectives chip. Renders
// nothing unless this browser tab just came from first-login completion
// (see useFirstSession's ARM doc-comment) and the player hasn't dismissed
// or already finished it. Fixed-position overlay (matches the other
// GameLayout chrome consumers -- MedalToast/NpcCombatBanner/
// PriorityHailConsumer's toast stack) so it never introduces a document
// scroll surface (Scroll Law) and sits compatibly with the inverted-L
// console grid rather than inside it.
//
// NO-CANON: the exact three objectives and their copy (player-journey.md
// names onboarding GOALS, not a scripted implementation) -- flagged for
// design sign-off.
const OBJECTIVES: Array<{ key: keyof FirstSessionProgress; label: string }> = [
  { key: 'dock', label: 'Dock at the station' },
  { key: 'trade', label: 'Make a trade' },
  { key: 'travel', label: 'Travel to a new sector' },
];

const FirstSessionObjectives: React.FC = () => {
  const { visible, progress, allComplete, dismiss } = useFirstSession();

  if (!visible) return null;

  return (
    <div className="first-session-chip" role="status" aria-live="polite">
      <div className="first-session-chip-header">
        <span className="first-session-chip-title">
          {allComplete ? 'Orientation Complete' : 'Getting Started'}
        </span>
        <button
          className="first-session-chip-dismiss"
          onClick={dismiss}
          aria-label="Dismiss orientation checklist"
        >
          ×
        </button>
      </div>
      <ul className="first-session-chip-list">
        {OBJECTIVES.map((objective) => (
          <li
            key={objective.key}
            className={`first-session-chip-item${progress[objective.key] ? ' first-session-chip-item--done' : ''}`}
          >
            <span className="first-session-chip-check" aria-hidden="true">
              {progress[objective.key] ? '✓' : '○'}
            </span>
            <span className="first-session-chip-label">{objective.label}</span>
          </li>
        ))}
      </ul>
    </div>
  );
};

export default FirstSessionObjectives;
