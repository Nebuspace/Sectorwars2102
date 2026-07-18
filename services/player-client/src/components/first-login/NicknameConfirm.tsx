import React, { useEffect, useReducer, useRef, useState } from 'react';
import {
  initNicknameConfirmState,
  nicknameConfirmReducer,
  NicknameResolution,
} from './nicknameConfirmLogic';
import './first-login.css';

interface NicknameConfirmProps {
  extractedName: string | null | undefined;
  disabled?: boolean;
  onResolved: (resolution: NicknameResolution) => void;
}

/**
 * Thin shell over nicknameConfirmLogic's pure reducer: renders the Yes/No
 * callsign prompt and (on a client-side rejection) a bounded free-text
 * retry field. Never calls the API itself -- it hands the final verdict to
 * onResolved, and the caller (OutcomeDisplay) makes the single real
 * POST /first-login/complete call.
 */
const NicknameConfirm: React.FC<NicknameConfirmProps> = ({ extractedName, disabled, onResolved }) => {
  const [state, dispatch] = useReducer(nicknameConfirmReducer, extractedName, initNicknameConfirmState);
  const [draft, setDraft] = useState('');
  const resolvedRef = useRef(false);

  // AC6 (resume): re-derive from the outcome payload whenever the extracted
  // name value changes, instead of trusting a one-shot local initializer.
  useEffect(() => {
    dispatch({ type: 'RESET', extractedName });
    resolvedRef.current = false;
    setDraft('');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [extractedName]);

  useEffect(() => {
    if (state.step === 'resolved' && state.resolution && !resolvedRef.current) {
      resolvedRef.current = true;
      onResolved(state.resolution);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.step, state.resolution]);

  if (state.step === 'skip' || state.step === 'resolved') {
    return null;
  }

  const reasonText: Record<string, string> = {
    length: 'Callsigns must be 3-20 characters long.',
    charset: 'Callsigns may only use letters, numbers, underscores, hyphens, and a single internal space.',
    profanity: 'That callsign isn\'t allowed.',
    taken: 'That callsign is already in use by another pilot.',
  };

  const handleSubmitDraft = (e: React.FormEvent) => {
    e.preventDefault();
    if (draft.trim() && !disabled) {
      dispatch({ type: 'SUBMIT_OVERRIDE', value: draft.trim() });
    }
  };

  if (state.step === 'prompt') {
    return (
      <div className="nickname-confirm">
        <div className="nickname-confirm-question">
          Register callsign "{state.extractedName}"?
        </div>
        <div className="nickname-confirm-buttons">
          <button
            type="button"
            className="outcome-start-button nickname-confirm-yes"
            onClick={() => dispatch({ type: 'CONFIRM' })}
            disabled={disabled}
          >
            Yes
          </button>
          <button
            type="button"
            className="nickname-confirm-decline"
            onClick={() => dispatch({ type: 'DECLINE' })}
            disabled={disabled}
          >
            No
          </button>
        </div>
      </div>
    );
  }

  // state.step === 'retry'
  return (
    <div className="nickname-confirm">
      <div className="nickname-confirm-reason">
        {state.reason ? reasonText[state.reason] : 'That callsign could not be registered.'}
      </div>
      <form onSubmit={handleSubmitDraft} className="nickname-confirm-retry-form">
        <input
          type="text"
          className="response-input nickname-confirm-input"
          placeholder="Try another callsign"
          value={draft}
          maxLength={20}
          onChange={(e) => setDraft(e.target.value)}
          disabled={disabled}
        />
        <div className="nickname-confirm-buttons">
          <button
            type="submit"
            className="outcome-start-button nickname-confirm-yes"
            disabled={disabled || !draft.trim()}
          >
            Register
          </button>
          <button
            type="button"
            className="nickname-confirm-decline"
            onClick={() => dispatch({ type: 'DECLINE' })}
            disabled={disabled}
          >
            Continue without a callsign
          </button>
        </div>
        <div className="nickname-confirm-hint">
          {state.attemptsRemaining} {state.attemptsRemaining === 1 ? 'attempt' : 'attempts'} remaining
        </div>
      </form>
    </div>
  );
};

export default NicknameConfirm;
