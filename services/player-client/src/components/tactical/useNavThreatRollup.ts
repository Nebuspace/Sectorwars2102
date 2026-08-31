import { useCallback, useEffect, useState } from 'react';
import { navAPI, type NavThreatEntry } from '../../services/api';
import { formatNavThreatError, navThreatEntriesToMap, type NavThreatMap } from './navThreat';

export interface NavThreatRollupState {
  map: NavThreatMap;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

export function useNavThreatRollup(): NavThreatRollupState {
  const [map, setMap] = useState<NavThreatMap>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    navAPI
      .getThreat()
      .then((rows: NavThreatEntry[]) => {
        setMap(navThreatEntriesToMap(rows));
        setError(null);
      })
      .catch((err: unknown) => {
        setMap({});
        setError(formatNavThreatError(err));
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { map, loading, error, refresh };
}
