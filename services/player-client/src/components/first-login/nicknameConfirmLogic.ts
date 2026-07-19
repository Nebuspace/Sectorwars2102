/**
 * Pure state machine for the first-login nickname-confirmation step
 * (WO-PUX-FLOGIN-NICKNAME). No I/O — NicknameConfirm.tsx is a thin shell
 * that dispatches actions here and calls POST /first-login/complete exactly
 * ONCE, after this reducer reaches a terminal 'resolved' step.
 *
 * Why only one /complete call: complete_first_login (gameserver
 * first_login_service.py) has no guard against being invoked more than
 * once for the same session — a second call re-deletes and re-creates the
 * player's starter ship and resets the ARIA relationship fields, it does
 * not just re-evaluate the nickname. So a "type another name, submit again"
 * retry loop against the real endpoint would silently repeat those
 * side effects. Instead, this reducer runs the whole Yes/No + free-text
 * retry conversation CLIENT-SIDE first (mirroring the server's length/
 * charset rules from nickname_validation_service.py), and only the single
 * final candidate — or a decline — is ever sent to /complete. The two
 * checks this module cannot mirror (profanity blocklist, uniqueness
 * against other players) can only be discovered by that one real call; a
 * rejection for either of those after the call lands is surfaced as an
 * informational notice (see OutcomeDisplay.tsx), not another retry round.
 */

export type NicknameRejectReason = 'length' | 'charset' | 'profanity' | 'taken';

export type NicknameConfirmStep = 'skip' | 'prompt' | 'retry' | 'resolved';

export interface NicknameResolution {
  confirmed: boolean;
  // Sent as nickname_override only when it differs from the originally
  // extracted name (a retry candidate); null means "use the extracted
  // name as-is" or "no nickname" per nickname_confirmed.
  override: string | null;
}

export interface NicknameConfirmState {
  step: NicknameConfirmStep;
  extractedName: string | null;
  reason: NicknameRejectReason | null;
  attemptsRemaining: number;
  resolution: NicknameResolution | null;
}

export type NicknameConfirmAction =
  | { type: 'CONFIRM' }
  | { type: 'DECLINE' }
  | { type: 'SUBMIT_OVERRIDE'; value: string }
  | { type: 'RESET'; extractedName: string | null | undefined };

export const RETRY_BUDGET = 2;

// Mirrors nickname_validation_service.py's NICKNAME_MIN_LEN / MAX_LEN /
// NICKNAME_PATTERN exactly (length 3-20; alphanumeric + underscore/hyphen,
// at most one internal space). Cannot mirror the profanity blocklist or
// the taken-name uniqueness check — those require server-side data.
const NICKNAME_MIN_LEN = 3;
const NICKNAME_MAX_LEN = 20;
const NICKNAME_PATTERN = /^[A-Za-z0-9_-]+(?: [A-Za-z0-9_-]+)?$/;

export interface NicknameClientValidation {
  ok: boolean;
  // Always present (null on success) rather than a discriminated union --
  // this project builds with tsconfig "strict": false (strictNullChecks
  // off), under which TS does not narrow `check.ok` to eliminate the
  // `{ ok: true }` member after an early return, so `check.reason` would
  // fail to typecheck on the fallthrough branch. A flat shape sidesteps
  // that entirely.
  reason: 'length' | 'charset' | null;
}

export function validateNicknameClientSide(
  name: string | null | undefined
): NicknameClientValidation {
  if (!name || name.length < NICKNAME_MIN_LEN || name.length > NICKNAME_MAX_LEN) {
    return { ok: false, reason: 'length' };
  }
  if (!NICKNAME_PATTERN.test(name)) {
    return { ok: false, reason: 'charset' };
  }
  return { ok: true, reason: null };
}

export function initNicknameConfirmState(
  extractedName: string | null | undefined
): NicknameConfirmState {
  const name = extractedName && extractedName.trim() ? extractedName.trim() : null;
  if (!name) {
    // AC2: no extracted name (incl. the escape-pod hard-fail path, which
    // never populates the key) -- skip the step, behave as a decline.
    return {
      step: 'skip',
      extractedName: null,
      reason: null,
      attemptsRemaining: RETRY_BUDGET,
      resolution: { confirmed: false, override: null },
    };
  }
  return {
    step: 'prompt',
    extractedName: name,
    reason: null,
    attemptsRemaining: RETRY_BUDGET,
    resolution: null,
  };
}

export function nicknameConfirmReducer(
  state: NicknameConfirmState,
  action: NicknameConfirmAction
): NicknameConfirmState {
  if (action.type === 'RESET') {
    // AC6 (resume): re-derive from the outcome payload whenever it changes,
    // never a one-shot local initializer.
    return initNicknameConfirmState(action.extractedName);
  }

  switch (state.step) {
    case 'skip':
    case 'resolved':
      return state; // terminal

    case 'prompt': {
      if (action.type === 'DECLINE') {
        return { ...state, step: 'resolved', resolution: { confirmed: false, override: null } };
      }
      if (action.type === 'CONFIRM') {
        const check = validateNicknameClientSide(state.extractedName);
        if (check.ok) {
          return {
            ...state,
            step: 'resolved',
            resolution: { confirmed: true, override: null },
          };
        }
        return { ...state, step: 'retry', reason: check.reason };
      }
      return state;
    }

    case 'retry': {
      if (action.type === 'DECLINE') {
        // Voluntary "continue without a callsign" bail-out.
        return { ...state, step: 'resolved', resolution: { confirmed: false, override: null } };
      }
      if (action.type === 'SUBMIT_OVERRIDE') {
        const check = validateNicknameClientSide(action.value);
        if (check.ok) {
          return {
            ...state,
            step: 'resolved',
            resolution: { confirmed: true, override: action.value.trim() },
          };
        }
        const attemptsRemaining = state.attemptsRemaining - 1;
        if (attemptsRemaining <= 0) {
          // 2-retry-exhaust -> proceed nameless, never blocks reaching /game.
          return {
            ...state,
            step: 'resolved',
            attemptsRemaining: 0,
            resolution: { confirmed: false, override: null },
          };
        }
        return { ...state, attemptsRemaining, reason: check.reason };
      }
      return state;
    }

    default:
      return state;
  }
}
