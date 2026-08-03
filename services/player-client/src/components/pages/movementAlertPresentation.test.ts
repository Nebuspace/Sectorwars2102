import { describe, it, expect } from 'vitest';
import { movementAlertVariant, movementAlertHeader, shouldShowMovementEncounters } from './movementAlertPresentation';

describe('movementAlertVariant', () => {
  it('is "success" when success is true', () => {
    expect(movementAlertVariant({ success: true, message: 'ok' })).toBe('success');
  });

  it('is "success" when success is omitted (legacy leavePlanet shape has no success field)', () => {
    expect(movementAlertVariant({ message: 'Successfully departed from planet' })).toBe('success');
  });

  it('is "error" when success is explicitly false', () => {
    expect(movementAlertVariant({ success: false, message: 'ERR_GATE_ACCESS_DENIED: gate access denied' })).toBe('error');
  });

  it('is "success" for a null/undefined result (no active alert)', () => {
    expect(movementAlertVariant(null)).toBe('success');
    expect(movementAlertVariant(undefined)).toBe('success');
  });
});

describe('movementAlertHeader', () => {
  it('shows the gate-denial header for an ERR_GATE_-prefixed failure message', () => {
    expect(movementAlertHeader({ success: false, message: 'ERR_GATE_REP_TOO_LOW: this warp gate requires...' }))
      .toBe('🚫 GATE ACCESS DENIED');
  });

  it('shows the generic refusal header for a non-gate failure message', () => {
    expect(movementAlertHeader({ success: false, message: 'Move timed out — server busy. Try again.' }))
      .toBe('⚠️ NAVIGATION REFUSED');
  });

  it('shows the generic refusal header for a failure with no message', () => {
    expect(movementAlertHeader({ success: false })).toBe('⚠️ NAVIGATION REFUSED');
  });

  it('shows the success header when success is true', () => {
    expect(movementAlertHeader({ success: true, message: 'Arrived' })).toBe('✅ NAVIGATION COMPLETE');
  });

  it('shows the success header for the legacy no-success-field shape', () => {
    expect(movementAlertHeader({ message: 'Successfully departed from planet' })).toBe('✅ NAVIGATION COMPLETE');
  });
});

describe('shouldShowMovementEncounters', () => {
  it('is true on success (encounter log is a success-only beat)', () => {
    expect(shouldShowMovementEncounters({ success: true, encounters: [{ type: 'players', players: [] }] })).toBe(true);
  });

  it('is true for the legacy no-success-field shape', () => {
    expect(shouldShowMovementEncounters({ message: 'ok' })).toBe(true);
  });

  it('is false on a gate-denied failure — a refused hop never departed', () => {
    expect(shouldShowMovementEncounters({ success: false, message: 'ERR_GATE_ACCESS_DENIED: gate access denied' })).toBe(false);
  });
});
