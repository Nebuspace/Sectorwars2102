/**
 * deckNavBus — a minimal, module-level pub/sub for cross-chrome deck-monitor
 * navigation requests (WO-UI1-CHROME-COMPLETE, item 6: annunciator lamp
 * click-through to the deck).
 *
 * WHY THIS EXISTS: TACTICAL's own [TARGET · THREAT] softkey is a LOCAL
 * `useState` inside TacticalMonitor.tsx (WO-UI2-DECK-RECONCILE) — the deck
 * monitors are self-contained, matching the ratified "One selection grammar:
 * a lit softkey under a screen" convention, with no shared nav context
 * governing which softkey is active. The annunciator's LAW/THREAT lamps
 * (Annunciator.tsx) live in GameLayout.tsx, well outside TacticalMonitor's
 * own subtree (TacticalMonitor mounts inside GameDashboard's console, itself
 * portaled into `.deck`, a SIBLING of the `.band` Annunciator lives in —
 * WO-UI0-SHELL-TRANSPLANT) — there is no ancestor/descendant relationship to
 * thread a prop or context selector through without restructuring the deck,
 * which this WO's scope explicitly forbids ("don't restructure the deck").
 *
 * This mirrors the SAME module-level pub/sub idiom already shipped in
 * services/resourceCatalog.ts + hooks/useResourceCatalog.ts (a plain
 * `Set` of listener callbacks, notified on write) — reuse, not invention.
 *
 * Requests are LATCHED (not just broadcast): `useTacticalPageRequest`
 * returns the most recent request even to a listener that subscribes AFTER
 * it fired, so a lamp click while docked/landed (TacticalMonitor unmounted
 * — it only renders in flight mode) still lands the correct softkey the
 * next time TacticalMonitor mounts. `requestId` is monotonic so a repeat
 * click on the SAME page (e.g. LAW clicked twice in a row) still re-fires
 * the effect that consumes it.
 */

export type TacticalPageId = 'target' | 'threat';

export interface TacticalPageRequest {
  page: TacticalPageId;
  requestId: number;
}

let currentRequest: TacticalPageRequest | null = null;
let nextRequestId = 1;
const listeners = new Set<(request: TacticalPageRequest) => void>();

/** Annunciator (or any future caller) asks the deck to switch TACTICAL's
 * active softkey. Fire-and-forget -- there is no synchronous confirmation
 * that a mounted TacticalMonitor picked it up (mirrors the rest of this
 * codebase's WS-signal idioms, e.g. WebSocketContext's npcCombatSignal). */
export function requestTacticalPage(page: TacticalPageId): void {
  const request: TacticalPageRequest = { page, requestId: nextRequestId++ };
  currentRequest = request;
  listeners.forEach((fn) => fn(request));
}

/** TacticalMonitor's own binding -- see hooks/useDeckNav.ts. Exported here
 * (rather than only from the hook) so a non-React caller (tests) can
 * subscribe directly without mounting a component. */
export function subscribeTacticalPageRequest(fn: (request: TacticalPageRequest) => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function getLatestTacticalPageRequest(): TacticalPageRequest | null {
  return currentRequest;
}

/** Test-only reset -- module state otherwise leaks a stale `currentRequest`/
 * `nextRequestId` across test files sharing this module instance. */
export function __resetDeckNavBusForTests(): void {
  currentRequest = null;
  nextRequestId = 1;
  listeners.clear();
}
