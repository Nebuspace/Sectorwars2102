/**
 * movementAlertPresentation — WO-WARP-GATE-FACTION-ACCESS.
 *
 * Pure, DOM/hook-free presentation logic for the movementResult cockpit
 * alert in GameDashboard.tsx. Extracted so the denial-vs-success branching
 * (CSS class, header copy, whether to show the encounter log) is unit
 * testable without mounting the full dashboard.
 *
 * Server-side, a gate-access refusal (warp_gate_service.check_traversal_access)
 * returns a message prefixed "ERR_GATE_..." with success: false — see
 * services/gameserver/src/services/warp_gate_service.py. Any other
 * success===false movement result (e.g. a non-gate refusal) still gets a
 * failure treatment, just with generic copy instead of the gate-specific one.
 */

export interface MovementAlertLike {
  success?: boolean;
  message?: string;
  encounters?: unknown[];
}

export type MovementAlertVariant = 'success' | 'error';

const GATE_DENIAL_PREFIX = 'ERR_GATE_';

/** CSS modifier class for the `.cockpit-alert` container. */
export function movementAlertVariant(result: MovementAlertLike | null | undefined): MovementAlertVariant {
  return result?.success === false ? 'error' : 'success';
}

/** Header copy: gate-specific denial, generic refusal, or success. */
export function movementAlertHeader(result: MovementAlertLike | null | undefined): string {
  if (result?.success === false) {
    return typeof result.message === 'string' && result.message.startsWith(GATE_DENIAL_PREFIX)
      ? '🚫 GATE ACCESS DENIED'
      : '⚠️ NAVIGATION REFUSED';
  }
  return '✅ NAVIGATION COMPLETE';
}

/** The encounter log is a success-only beat — a refused hop never departed. */
export function shouldShowMovementEncounters(result: MovementAlertLike | null | undefined): boolean {
  return result?.success !== false;
}
