import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { useAuth } from './AuthContext';
import { firstLoginAPI } from '../services/api';

// Types for first login state
export interface FirstLoginSession {
  session_id: string;
  player_id: string;
  available_ships: string[];
  current_step: 'ship_selection' | 'dialogue' | 'completion';
  npc_prompt: string;
  exchange_id?: string;
  sequence_number?: number;
  ship_claimed?: string;
  // WO-PUX-FLOGIN-RESUME: persisted guard identity, sourced from the server
  // (src/utils/guard_personalities.py) instead of a client-side hash mirror.
  guard_name?: string;
  guard_title?: string;
  guard_trait?: string;
  guard_base_suspicion?: number;
  guard_description?: string;
}

export interface DialogueAnalysis {
  exchange_id: string;
  analysis: {
    persuasiveness: number;
    confidence: number;
    consistency: number;
  };
  is_final: boolean;
  outcome?: {
    outcome: string;
    awarded_ship: string;
    starting_credits: number;
    negotiation_skill: string;
    final_persuasion_score: number;
    negotiation_bonus: boolean;
    notoriety_penalty: boolean;
    guard_response: string;
    // WO-PUX-FLOGIN-NICKNAME: present only on outcomes eligible for the
    // nickname-confirmation prompt (absent on the escape-pod hard-fail
    // path) -- carried through both the live dialogue response and the
    // session-resume payload so a reload doesn't lose the pending prompt.
    extracted_player_name?: string | null;
  };
  next_question?: string;
  next_exchange_id?: string;
}

// The confirm/decline verdict collected by NicknameConfirm.tsx before the
// single POST /first-login/complete call (see nicknameConfirmLogic.ts for
// why this is never round-tripped more than once).
export interface NicknameVerdict {
  confirmed: boolean;
  override: string | null;
}

export interface CompleteFirstLoginResult {
  player_id: string;
  nickname?: string | null;
  credits: number;
  ship: {
    id: string;
    name: string;
    type: string;
  };
  negotiation_bonus: boolean;
  notoriety_penalty: boolean;
  // Set only when nickname_confirmed was sent true and server-side
  // validation rejected the candidate (length/charset/profanity/taken).
  // Completion still succeeds -- the client surfaces this as an
  // informational notice, never a blocker.
  nickname_rejected_reason?: 'length' | 'charset' | 'profanity' | 'taken' | null;
}

// WO-PUX-FLOGIN-IDEMPOTENT: thrown by completeFirstLogin instead of the raw
// axios error when the server's idempotency guard reports HTTP 400 "First
// login already completed". That happens when an earlier /complete call
// already succeeded server-side but its response never reached this client
// (timeout, dropped connection, a manual retry) -- it is not a real
// failure. Callers should recover by re-checking status via
// checkFirstLoginStatus() and proceeding if it confirms completion, never
// by surfacing this as a dead-end error.
export class FirstLoginAlreadyCompletedError extends Error {
  constructor() {
    super('First login already completed');
    this.name = 'FirstLoginAlreadyCompletedError';
  }
}

/** True when message is bare transport collapse, not gameserver first-login detail. */
const isFirstLoginNetworkCollapse = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed)
  );
};

/** Collapse TypeError/network tokens to caller-provided fallback (LEG-3318). */
export function formatFirstLoginError(err: unknown, fallback: string): string {
  const message = err instanceof Error ? err.message : undefined;
  const hasServerDetail =
    !(err instanceof TypeError) &&
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim()) &&
    !isFirstLoginNetworkCollapse(message);

  if (hasServerDetail) return message!;
  return fallback;
}

interface FirstLoginContextType {
  requiresFirstLogin: boolean;
  isLoading: boolean;
  error: string | null;
  // Re-checks first-login status against the server and returns the fresh
  // requires_first_login value directly (undefined if the check itself
  // failed) -- callers recovering from a lost response need the value
  // synchronously rather than waiting on a later re-render.
  checkFirstLoginStatus: () => Promise<boolean | undefined>;

  // Session data
  session: FirstLoginSession | null;
  startSession: () => Promise<void>;
  
  // Dialogue state
  currentPrompt: string;
  exchangeId: string | null;
  dialogueHistory: {
    npc: string;
    player: string;
    consistency?: number;
    confidence?: number;
    persuasiveness?: number;
  }[];
  
  // Ship selection
  availableShips: string[];
  sessionLoaded: boolean;
  claimShip: (shipType: string, response: string) => Promise<void>;
  
  // Dialogue interaction
  submitResponse: (response: string) => Promise<DialogueAnalysis>;
  
  // Dialogue outcome
  dialogueOutcome: DialogueAnalysis['outcome'] | null;
  completeFirstLogin: (verdict?: NicknameVerdict) => Promise<CompleteFirstLoginResult>;
  
  // UI state helpers
  resetError: () => void;
  resetSession: () => void;
}

const FirstLoginContext = createContext<FirstLoginContextType | undefined>(undefined);

export const FirstLoginProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const { user, isAuthenticated } = useAuth();
  
  // Clamp analysis scores to valid range (0-100), handling NaN/undefined
  const clampScore = (v: unknown): number | undefined => {
    if (v === undefined || v === null) return undefined;
    const n = Number(v);
    if (!Number.isFinite(n)) return undefined;
    return Math.max(0, Math.min(100, n));
  };

  // Basic state
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [requiresFirstLogin, setRequiresFirstLogin] = useState<boolean>(false);
  
  // Session state
  const [session, setSession] = useState<FirstLoginSession | null>(null);
  const [dialogueHistory, setDialogueHistory] = useState<{ npc: string; player: string; consistency?: number; confidence?: number; persuasiveness?: number; }[]>([]);
  const [currentPrompt, setCurrentPrompt] = useState<string>('');
  const [exchangeId, setExchangeId] = useState<string | null>(null);
  const [dialogueOutcome, setDialogueOutcome] = useState<DialogueAnalysis['outcome'] | null>(null);
  
  // Rate limiting state
  const [lastCheckTime, setLastCheckTime] = useState<number>(0);
  const [lastSessionTime, setLastSessionTime] = useState<number>(0);
  const CHECK_COOLDOWN = 5000; // 5 seconds between checks
  const SESSION_COOLDOWN = 5000; // 5 seconds between session starts

  // Check if first login is required when user logs in
  useEffect(() => {
    if (isAuthenticated && user) {
      const now = Date.now();
      if (now - lastCheckTime > CHECK_COOLDOWN) {
        setLastCheckTime(now);
        checkFirstLoginStatus();
      }
    }
  }, [isAuthenticated, user, lastCheckTime]);
  
  // Check if the player needs to go through first login. Returns the fresh
  // requires_first_login value directly (undefined on failure) so callers
  // that need it synchronously -- e.g. idempotent-completion recovery --
  // don't have to wait on a later re-render of the reactive state.
  const checkFirstLoginStatus = async (): Promise<boolean | undefined> => {
    setIsLoading(true);
    setError(null);

    try {
      const data = (await firstLoginAPI.getStatus()) as any;
      const requiresFirst = data.requires_first_login;
      setRequiresFirstLogin(requiresFirst);

      // If first login is required and there's an active session, load it
      if (requiresFirst && data.session_id) {
        await startSession();
      }

      return requiresFirst;
    } catch (error) {
      console.error('Error checking first login status:', error);
      setError(formatFirstLoginError(error, 'Failed to check first login status.'));
      return undefined;
    } finally {
      setIsLoading(false);
    }
  };
  
  // Start or resume a first login session
  const startSession = async () => {
    // Rate limiting check
    const now = Date.now();
    if (now - lastSessionTime < SESSION_COOLDOWN) {
      return;
    }
    setLastSessionTime(now);
    
    setIsLoading(true);
    setError(null);

    try {
      const data = (await firstLoginAPI.startSession()) as any;

      setSession(data as FirstLoginSession);
      setCurrentPrompt(data.npc_prompt);
      setExchangeId(data.exchange_id || null);

      if (data.resumed) {
        // Replay the full persisted history instead of starting fresh — the
        // only DELETE this context ever issues is the user-invoked explicit
        // reset (resetSession), never an automatic one on reload.
        const history = (data.dialogue_history || []).map((exchange: any) => ({
          npc: exchange.npc_prompt,
          player: exchange.player_response,
          consistency: clampScore(exchange.consistency),
          confidence: clampScore(exchange.confidence),
          persuasiveness: clampScore(exchange.persuasiveness),
        }));
        setDialogueHistory(history);

        // Resuming into the completion step: hydrate the outcome too, or
        // OutcomeDisplay has nothing to render and the screen goes blank.
        if (data.current_step === 'completion' && data.outcome) {
          setDialogueOutcome(data.outcome);
        }
      } else {
        // Fresh session — initialize dialogue history with the first NPC prompt.
        setDialogueHistory([{ npc: data.npc_prompt, player: '' }]);
      }
    } catch (error: any) {
      console.error('Error starting first login session:', error);
      const status = error?.status ?? error?.response?.status;
      
      // Handle specific error types
      if (status === 429) {
        setError('Too many requests. Please wait a moment.');
        // Retry after a longer delay for rate limiting
        setTimeout(() => {
          startSession();
        }, 10000); // 10 seconds
        return;
      } else if (status === 500) {
        setError('Server error. Please try again in a few moments.');
      } else {
        setError(formatFirstLoginError(error, 'Failed to start first login session.'));
      }
    } finally {
      setIsLoading(false);
    }
  };
  
  // Claim a ship and submit initial dialogue response
  const claimShip = async (shipType: string, response: string) => {
    setIsLoading(true);
    setError(null);

    try {
      const payload = {
        ship_type: shipType,
        dialogue_response: response
      };

      const data = (await firstLoginAPI.claimShip(payload)) as any;

      setSession(data);

      // Check if this is an immediate outcome (e.g., Escape Pod auto-approval)
      if (data.current_step === 'completion' && data.outcome) {

        // Set the outcome directly
        setDialogueOutcome(data.outcome);

        // Update dialogue history with approval message and any analysis scores
        setDialogueHistory(prev => [
          ...prev,
          {
            npc: '',
            player: response,
            consistency: clampScore(data.analysis?.consistency),
            confidence: clampScore(data.analysis?.confidence),
            persuasiveness: clampScore(data.analysis?.persuasiveness),
          },
          { npc: data.npc_prompt, player: '' }
        ]);

        setCurrentPrompt(data.npc_prompt);
      } else {
        // Normal flow: received a question for interrogation
        // Update dialogue history with any analysis scores
        setDialogueHistory(prev => [
          ...prev,
          {
            npc: '',
            player: response,
            consistency: clampScore(data.analysis?.consistency),
            confidence: clampScore(data.analysis?.confidence),
            persuasiveness: clampScore(data.analysis?.persuasiveness),
          },
          { npc: data.npc_prompt, player: '' }
        ]);

        // Set new prompt and exchange ID
        setCurrentPrompt(data.npc_prompt);
        setExchangeId(data.exchange_id || null);
      }
    } catch (error: any) {
      const status = error?.status ?? error?.response?.status;
      const detail = error?.data?.detail ?? error?.response?.data?.detail;
      console.error('FirstLogin: Error claiming ship:', status, detail || error.message);
      
      // More specific error messages
      if (status === 401) {
        setError('Authentication failed. Please log in again.');
      } else if (status === 400) {
        setError(detail || 'Invalid ship selection or response.');
      } else if (status === 500) {
        setError('Server error. Please try again later.');
      } else if (error.code === 'ERR_NETWORK') {
        setError('Network error. Please check your connection.');
      } else {
        setError(formatFirstLoginError(error, 'Failed to claim ship. Please try again.'));
      }
    } finally {
      setIsLoading(false);
    }
  };
  
  // Submit a dialogue response
  const submitResponse = async (response: string): Promise<DialogueAnalysis> => {
    setIsLoading(true);
    setError(null);

    try {
      if (!exchangeId) {
        throw new Error('No active dialogue exchange.');
      }

      const data = (await firstLoginAPI.submitDialogue(exchangeId, response)) as DialogueAnalysis;

      // Update dialogue history with player response and analysis scores
      setDialogueHistory(prev => [
        ...prev.slice(0, prev.length - 1),
        {
          ...prev[prev.length - 1],
          player: response,
          consistency: data.analysis?.consistency,
          confidence: data.analysis?.confidence,
          persuasiveness: data.analysis?.persuasiveness,
        }
      ]);

      // If there's a next question, add it to history and update state
      if (data.next_question) {
        setDialogueHistory(prev => [
          ...prev,
          { npc: data.next_question!, player: '' }
        ]);
        setCurrentPrompt(data.next_question);
        setExchangeId(data.next_exchange_id || null);
      }

      // If this is the final response, store the outcome
      if (data.is_final && data.outcome) {
        const outcome = data.outcome;

        setDialogueOutcome(outcome);

        // Add the guard's final response to the history
        setDialogueHistory(prev => [
          ...prev,
          { npc: outcome.guard_response, player: '' }
        ]);

        // Update the current prompt
        setCurrentPrompt(outcome.guard_response);
      }

      return data;
    } catch (error) {
      console.error('[FirstLogin:Error] Dialogue submission failed:', error);
      setError('Failed to submit dialogue response.');
      throw error;
    } finally {
      setIsLoading(false);
    }
  };
  
  // Complete the first login process. `verdict` carries the player's
  // nickname-confirmation decision (WO-PUX-FLOGIN-NICKNAME); omitting it
  // (a body-less call) is a decline, matching the server's pre-existing
  // default -- the nickname stays null exactly as it did before this
  // feature shipped.
  const completeFirstLogin = async (verdict?: NicknameVerdict): Promise<CompleteFirstLoginResult> => {
    setIsLoading(true);
    setError(null);

    try {
      const body = verdict
        ? { nickname_confirmed: verdict.confirmed, nickname_override: verdict.override }
        : undefined;
      const data = (await firstLoginAPI.complete(body)) as CompleteFirstLoginResult;

      // First login is now complete
      setRequiresFirstLogin(false);

      return data;
    } catch (error: any) {
      // WO-PUX-FLOGIN-IDEMPOTENT: the server's idempotency guard returns
      // HTTP 400 "First login already completed" when an earlier /complete
      // call already succeeded but its response was lost before reaching
      // this client. That's not a real failure -- throw a distinguishable
      // error so the caller can recover (re-check status, proceed) instead
      // of dead-ending the player. Leave `error` state untouched here so a
      // recoverable condition never renders as a failure.
      // Supports both apiRequest-shaped errors (.status/.data) and raw axios
      // (.response) from older test harnesses / passthrough rejects.
      const status = error?.status ?? error?.response?.status;
      const detail = error?.data?.detail ?? error?.response?.data?.detail;
      if (status === 400 && typeof detail === 'string' && /already completed/i.test(detail)) {
        console.warn('[FirstLogin] /complete reported already-completed; caller should recover via status re-check.');
        throw new FirstLoginAlreadyCompletedError();
      }

      console.error('[FirstLogin:Error] Completion failed:', error);
      setError('Failed to complete first login process.');
      throw error;
    } finally {
      setIsLoading(false);
    }
  };
  
  // Reset error state
  const resetError = () => setError(null);
  
  const resetSession = async () => {
    try {
      // Clear frontend state first
      setSession(null);
      setDialogueHistory([]);
      setCurrentPrompt('');
      setExchangeId(null);
      setDialogueOutcome(null);
      setError(null);
      
      // Try to reset server-side session
      await firstLoginAPI.resetSession();
    } catch {
      // Server cleanup is non-critical
      // Don't show error to user as this is just a cleanup attempt
    }
  };
  
  // Context value
  const value = {
    requiresFirstLogin,
    isLoading,
    error,
    checkFirstLoginStatus,

    session,
    startSession,
    
    currentPrompt,
    exchangeId,
    dialogueHistory,
    
    availableShips: session?.available_ships || [],
    sessionLoaded: !!session,
    claimShip,
    
    submitResponse,
    
    dialogueOutcome,
    completeFirstLogin,
    
    resetError,
    resetSession
  };
  
  return <FirstLoginContext.Provider value={value}>{children}</FirstLoginContext.Provider>;
};

// Hook for using the first login context
export const useFirstLogin = () => {
  const context = useContext(FirstLoginContext);
  if (context === undefined) {
    throw new Error('useFirstLogin must be used within a FirstLoginProvider');
  }
  return context;
};