/**
 * spacedockVenueBus — latched pub/sub for opening a SpaceDock venue from
 * outside SpaceDockInterface (e.g. mining license expiry WS → Astral Mining).
 *
 * Mirrors deckNavBus: requests latch so a subscriber mounting after the
 * signal still picks up the most recent venue request.
 */

export type SpacedockVenueId = 'mining';

export interface SpacedockVenueRequest {
  venue: SpacedockVenueId;
  requestId: number;
}

let currentRequest: SpacedockVenueRequest | null = null;
let nextRequestId = 1;
const listeners = new Set<(request: SpacedockVenueRequest) => void>();

export function requestSpacedockVenue(venue: SpacedockVenueId): void {
  const request: SpacedockVenueRequest = { venue, requestId: nextRequestId++ };
  currentRequest = request;
  listeners.forEach((fn) => fn(request));
}

export function subscribeSpacedockVenueRequest(
  fn: (request: SpacedockVenueRequest) => void,
): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function getLatestSpacedockVenueRequest(): SpacedockVenueRequest | null {
  return currentRequest;
}

/** Test-only reset — clears latched state between Vitest files. */
export function __resetSpacedockVenueBusForTests(): void {
  currentRequest = null;
  nextRequestId = 1;
  listeners.clear();
}
