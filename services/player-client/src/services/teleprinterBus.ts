/**
 * teleprinterBus — latched pub/sub for cross-chrome ARIA PANEL requests.
 *
 * Mirrors deckNavBus: NAV 3D (and future callers) can request PANEL mode
 * without coupling to GameLayout's teleprinterBodyPanel useState. Requests
 * are latched so a subscriber that mounts after the fire still sees the
 * most recent open request via requestId.
 */

export interface TeleprinterPanelRequest {
  open: boolean;
  requestId: number;
}

let currentRequest: TeleprinterPanelRequest | null = null;
let nextRequestId = 1;
const listeners = new Set<(request: TeleprinterPanelRequest) => void>();

/** Ask GameLayout to open (or close) the ARIA teleprinter PANEL. */
export function requestTeleprinterPanel(open: boolean): void {
  const request: TeleprinterPanelRequest = { open, requestId: nextRequestId++ };
  currentRequest = request;
  listeners.forEach((fn) => fn(request));
}

export function subscribeTeleprinterPanelRequest(
  fn: (request: TeleprinterPanelRequest) => void,
): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function getLatestTeleprinterPanelRequest(): TeleprinterPanelRequest | null {
  return currentRequest;
}

/** Test-only reset — module state otherwise leaks across test files. */
export function __resetTeleprinterBusForTests(): void {
  currentRequest = null;
  nextRequestId = 1;
  listeners.clear();
}
