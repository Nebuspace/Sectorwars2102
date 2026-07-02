/**
 * useResourceCatalog — React binding over services/resourceCatalog.ts.
 *
 * Triggers the (session-cached, shared) GET /api/v1/resources fetch on first
 * mount and re-renders every subscribed consumer once it lands. See
 * resourceCatalog.ts for the known auth-dependency gap this degrades
 * gracefully around: a failed fetch leaves `catalog` empty rather than
 * throwing, so callers should treat [] as "no catalog available yet /
 * unavailable this session" and design their UI accordingly (e.g. an
 * options list that's just shorter, never a crash).
 */
import { useCallback, useEffect, useState } from 'react';
import {
  getCachedResourceCatalog,
  getResourceCatalog,
  resourceLabel,
  subscribeResourceCatalog,
  type ResourceCatalogEntry,
} from '../services/resourceCatalog';

export interface UseResourceCatalogResult {
  /** The full catalog, or [] before the first fetch resolves (or if it failed). */
  catalog: ResourceCatalogEntry[];
  /** True until the catalog has loaded at least once (this session) or failed. */
  loading: boolean;
  /** Registry label -> prettified key. */
  getLabel: (name: string) => string;
}

export function useResourceCatalog(): UseResourceCatalogResult {
  const [catalog, setCatalog] = useState<ResourceCatalogEntry[] | null>(getCachedResourceCatalog());
  const [loading, setLoading] = useState<boolean>(getCachedResourceCatalog() === null);

  useEffect(() => {
    let cancelled = false;
    if (!getCachedResourceCatalog()) {
      getResourceCatalog()
        .then((data) => {
          if (!cancelled) {
            setCatalog(data);
            setLoading(false);
          }
        })
        .catch(() => {
          // Leave catalog empty — getLabel already degrades to prettify(), so
          // a failed fetch never blanks the UI, just shortens the option list.
          if (!cancelled) setLoading(false);
        });
    }
    // Catches the case where another consumer's fetch resolves first.
    const unsubscribe = subscribeResourceCatalog(() => {
      if (!cancelled) {
        setCatalog(getCachedResourceCatalog());
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, []);

  const getLabel = useCallback((name: string) => resourceLabel(name, catalog), [catalog]);

  return { catalog: catalog || [], loading, getLabel };
}
