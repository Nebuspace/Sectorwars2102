/**
 * nicknameConfirmLogic — pure state machine transitions (WO-PUX-FLOGIN-NICKNAME).
 */
import { describe, it, expect } from 'vitest';
import {
  initNicknameConfirmState,
  nicknameConfirmReducer,
  validateNicknameClientSide,
  RETRY_BUDGET,
} from '../nicknameConfirmLogic';

describe('validateNicknameClientSide', () => {
  it('rejects names shorter than 3 chars', () => {
    expect(validateNicknameClientSide('ab')).toEqual({ ok: false, reason: 'length' });
  });

  it('rejects names longer than 20 chars', () => {
    expect(validateNicknameClientSide('a'.repeat(21))).toEqual({ ok: false, reason: 'length' });
  });

  it('rejects null/empty', () => {
    expect(validateNicknameClientSide(null)).toEqual({ ok: false, reason: 'length' });
    expect(validateNicknameClientSide('')).toEqual({ ok: false, reason: 'length' });
  });

  it('rejects more than one internal space (mirrors server charset rule)', () => {
    expect(validateNicknameClientSide('Captain Zara Vex')).toEqual({ ok: false, reason: 'charset' });
  });

  it('rejects disallowed punctuation', () => {
    expect(validateNicknameClientSide('Zara!')).toEqual({ ok: false, reason: 'charset' });
  });

  it('accepts a valid single-word name', () => {
    expect(validateNicknameClientSide('Zara')).toEqual({ ok: true, reason: null });
  });

  it('accepts a valid two-word name with one internal space', () => {
    expect(validateNicknameClientSide('Zara Vex')).toEqual({ ok: true, reason: null });
  });

  it('accepts underscores and hyphens', () => {
    expect(validateNicknameClientSide('Zara_Vex-9')).toEqual({ ok: true, reason: null });
  });
});

describe('initNicknameConfirmState', () => {
  it('no-name -> skip (also resolves declined, so callers can complete unconditionally)', () => {
    const state = initNicknameConfirmState(null);
    expect(state.step).toBe('skip');
    expect(state.resolution).toEqual({ confirmed: false, override: null });
  });

  it('undefined -> skip', () => {
    expect(initNicknameConfirmState(undefined).step).toBe('skip');
  });

  it('whitespace-only -> skip', () => {
    expect(initNicknameConfirmState('   ').step).toBe('skip');
  });

  it('a real extracted name -> prompt, unresolved', () => {
    const state = initNicknameConfirmState('Zara');
    expect(state.step).toBe('prompt');
    expect(state.extractedName).toBe('Zara');
    expect(state.resolution).toBeNull();
    expect(state.attemptsRemaining).toBe(RETRY_BUDGET);
  });

  it('trims surrounding whitespace off the extracted name', () => {
    expect(initNicknameConfirmState('  Zara  ').extractedName).toBe('Zara');
  });
});

describe('nicknameConfirmReducer', () => {
  it('yes -> confirmed (valid extracted name resolves immediately, no override)', () => {
    const start = initNicknameConfirmState('Zara');
    const next = nicknameConfirmReducer(start, { type: 'CONFIRM' });
    expect(next.step).toBe('resolved');
    expect(next.resolution).toEqual({ confirmed: true, override: null });
  });

  it('no -> declined', () => {
    const start = initNicknameConfirmState('Zara');
    const next = nicknameConfirmReducer(start, { type: 'DECLINE' });
    expect(next.step).toBe('resolved');
    expect(next.resolution).toEqual({ confirmed: false, override: null });
  });

  it('reject-reason -> retry (extracted name fails client-side charset check)', () => {
    const start = initNicknameConfirmState('Captain Zara Vex'); // 2 internal spaces
    const next = nicknameConfirmReducer(start, { type: 'CONFIRM' });
    expect(next.step).toBe('retry');
    expect(next.reason).toBe('charset');
    expect(next.resolution).toBeNull();
    expect(next.attemptsRemaining).toBe(RETRY_BUDGET);
  });

  it('a valid free-text retry resolves confirmed with the override candidate', () => {
    let state = initNicknameConfirmState('Captain Zara Vex');
    state = nicknameConfirmReducer(state, { type: 'CONFIRM' }); // -> retry
    state = nicknameConfirmReducer(state, { type: 'SUBMIT_OVERRIDE', value: 'ZaraV' });
    expect(state.step).toBe('resolved');
    expect(state.resolution).toEqual({ confirmed: true, override: 'ZaraV' });
  });

  it('an invalid free-text retry consumes a budget slot and stays in retry', () => {
    let state = initNicknameConfirmState('Captain Zara Vex');
    state = nicknameConfirmReducer(state, { type: 'CONFIRM' }); // -> retry, attemptsRemaining=2
    state = nicknameConfirmReducer(state, { type: 'SUBMIT_OVERRIDE', value: 'x' }); // too short
    expect(state.step).toBe('retry');
    expect(state.reason).toBe('length');
    expect(state.attemptsRemaining).toBe(1);
  });

  it('2-retry-exhaust -> proceed (declined, nameless, never blocks completion)', () => {
    let state = initNicknameConfirmState('Captain Zara Vex');
    state = nicknameConfirmReducer(state, { type: 'CONFIRM' }); // -> retry, attemptsRemaining=2
    state = nicknameConfirmReducer(state, { type: 'SUBMIT_OVERRIDE', value: 'x' }); // attemptsRemaining=1
    expect(state.step).toBe('retry');
    state = nicknameConfirmReducer(state, { type: 'SUBMIT_OVERRIDE', value: 'y' }); // exhausted
    expect(state.step).toBe('resolved');
    expect(state.attemptsRemaining).toBe(0);
    expect(state.resolution).toEqual({ confirmed: false, override: null });
  });

  it('allows voluntarily declining mid-retry ("continue without a callsign")', () => {
    let state = initNicknameConfirmState('Captain Zara Vex');
    state = nicknameConfirmReducer(state, { type: 'CONFIRM' }); // -> retry
    state = nicknameConfirmReducer(state, { type: 'DECLINE' });
    expect(state.step).toBe('resolved');
    expect(state.resolution).toEqual({ confirmed: false, override: null });
  });

  it('is a no-op once resolved (terminal state ignores further actions)', () => {
    let state = initNicknameConfirmState('Zara');
    state = nicknameConfirmReducer(state, { type: 'DECLINE' });
    const resolved = state;
    state = nicknameConfirmReducer(state, { type: 'CONFIRM' });
    expect(state).toEqual(resolved);
  });

  it('is a no-op in skip state (no extracted name)', () => {
    const start = initNicknameConfirmState(null);
    const next = nicknameConfirmReducer(start, { type: 'CONFIRM' });
    expect(next).toEqual(start);
  });

  it('RESET re-derives state from a new outcome payload at any point (resume, AC6)', () => {
    let state = initNicknameConfirmState('Zara');
    state = nicknameConfirmReducer(state, { type: 'CONFIRM' }); // resolved
    expect(state.step).toBe('resolved');

    // A reload hands the component a freshly-hydrated outcome with a
    // different (or the same) extracted name -- state must re-derive, not
    // stay stuck on stale local state.
    state = nicknameConfirmReducer(state, { type: 'RESET', extractedName: 'Vex' });
    expect(state.step).toBe('prompt');
    expect(state.extractedName).toBe('Vex');
    expect(state.resolution).toBeNull();
  });

  it('RESET to no name goes to skip even from a mid-retry state', () => {
    let state = initNicknameConfirmState('Captain Zara Vex');
    state = nicknameConfirmReducer(state, { type: 'CONFIRM' }); // -> retry
    state = nicknameConfirmReducer(state, { type: 'RESET', extractedName: null });
    expect(state.step).toBe('skip');
  });
});
