/**
 * useDeckNav — React binding over services/deckNavBus.ts (WO-UI1-CHROME-
 * COMPLETE item 6). Mirrors hooks/useResourceCatalog.ts's binding pattern
 * over services/resourceCatalog.ts: a thin useState+subscribe wrapper, all
 * the actual pub/sub logic lives in the plain module.
 */
import { useEffect, useState } from 'react';
import {
  getLatestTacticalPageRequest,
  requestTacticalPage,
  subscribeTacticalPageRequest,
  type TacticalPageId,
  type TacticalPageRequest,
} from '../services/deckNavBus';

export type { TacticalPageId, TacticalPageRequest };
export { requestTacticalPage };

/** TacticalMonitor's consumer half: the latest tactical-page navigation
 * request, whether it fired before or after this hook mounted (picks up a
 * pending request left over from a lamp click while docked/landed). Bump
 * `requestId` even for a repeat click on the same page, so a caller's
 * `useEffect([request])` re-fires every time, not just on page CHANGE. */
export function useTacticalPageRequest(): TacticalPageRequest | null {
  const [request, setRequest] = useState<TacticalPageRequest | null>(() => getLatestTacticalPageRequest());

  useEffect(() => {
    // Pick up anything that fired between this hook's initial render and
    // this effect running (rare, but the state read above is a snapshot).
    const latest = getLatestTacticalPageRequest();
    if (latest && latest !== request) setRequest(latest);
    return subscribeTacticalPageRequest(setRequest);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return request;
}
