/**
 * useResourceCatalog — React binding over services/resourceCatalog.ts.
 *
 * Triggers the (session-cached, shared) GET /api/v1/resources fetch on first
 * mount and re-renders every subscribed consumer once it lands. Returns
 * label/icon/colour lookups that degrade gracefully before the catalog has
 * loaded or for an unknown key — see resourceCatalog.ts for the fallback
 * chain (registry label -> site default -> prettified key; icon/colour are
 * always local defaults, the catalog carries no glyph/colour data yet).
 */
import { useCallback, useEffect, useState } from 'react';
import {
  getCachedResourceCatalog,
  getResourceCatalog,
  resourceColor,
  resourceIcon,
  resourceLabel,
  subscribeResourceCatalog,
  type ResourceCatalogEntry,
} from '../services/resourceCatalog';

export interface UseResourceCatalogResult {
  /** The full catalog, or [] before the first fetch resolves. */
  catalog: ResourceCatalogEntry[];
  /** True until the catalog has loaded at least once (this session). */
  loading: boolean;
  /** Registry label -> site default -> prettified key. */
  getLabel: (name: string) => string;
  /** Hand-picked default glyph -> generic. */
  getIcon: (name: string) => string;
  /** Hand-picked default colour -> generic. */
  getColor: (name: string) => string;
}

export function useResourceCatalog(): UseResourceCatalogResult {
  const [catalog, setCatalog] = useState<ResourceCatalogEntry[] | null>(getCachedResourceCatalog());

  useEffect(() => {
    let cancelled = false;
    if (!getCachedResourceCatalog()) {
      getResourceCatalog()
        .then((data) => {
          if (!cancelled) setCatalog(data);
        })
        .catch(() => {
          // Leave catalog null — getLabel/getIcon/getColor already degrade to
          // their fallback chains, so a failed fetch never blanks the UI.
        });
    }
    // Catches the case where another consumer's fetch resolves first.
    const unsubscribe = subscribeResourceCatalog(() => {
      if (!cancelled) setCatalog(getCachedResourceCatalog());
    });
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, []);

  const getLabel = useCallback((name: string) => resourceLabel(name, catalog), [catalog]);
  const getIcon = useCallback((name: string) => resourceIcon(name), []);
  const getColor = useCallback((name: string) => resourceColor(name), []);

  return { catalog: catalog || [], loading: catalog === null, getLabel, getIcon, getColor };
}
