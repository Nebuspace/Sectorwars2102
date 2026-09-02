/**
 * LEG-46 — ARIA exploration-map suggestion panel (aria-companion.md § Exploration suggestions).
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  ariaExplorationAPI,
  type ExplorationSuggestion,
} from '../../services/api';
import './exploration-suggestions.css';

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed) ||
    /^networkerror$/i.test(trimmed)
  );
};

export function formatExplorationSuggestionError(err: unknown): string {
  const fallback = 'Failed to load exploration suggestions';
  if (err instanceof TypeError) return fallback;
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  const hasServerDetail =
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim()) &&
    !isNetworkCollapseMessage(message);
  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'You do not have permission to load exploration suggestions.';
  }

  if (status === 429) {
    return 'Exploration suggestions rate limit exceeded — wait a moment and try again.';
  }

  if ((status === 500 || status === 503) && hasServerDetail) return message!;
  if (hasServerDetail) return message!;
  return fallback;
}

const kindLabel: Record<ExplorationSuggestion['kind'], string> = {
  repeat_visit: 'repeat',
  expand: 'frontier',
  risky: 'risk',
};

const ExplorationSuggestionPanel: React.FC = () => {
  const [suggestions, setSuggestions] = useState<ExplorationSuggestion[]>([]);
  const [emptyMessage, setEmptyMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await ariaExplorationAPI.getSuggestions();
      setSuggestions(payload.suggestions ?? []);
      setEmptyMessage(payload.empty_message ?? null);
    } catch (err) {
      setError(formatExplorationSuggestionError(err));
      setSuggestions([]);
      setEmptyMessage(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className="aria-exploration-panel" aria-label="ARIA exploration suggestions">
      <h3>EXPLORATION INTEL</h3>
      {loading && <p className="empty">Scanning your exploration map…</p>}
      {!loading && error && <p className="error" role="alert">{error}</p>}
      {!loading && !error && suggestions.length === 0 && (
        <p className="empty">{emptyMessage ?? 'No suggestions yet.'}</p>
      )}
      {!loading && !error && suggestions.length > 0 && (
        <ul>
          {suggestions.map((item) => (
            <li key={`${item.kind}-${item.sector_id}`}>
              <span className="kind">{kindLabel[item.kind] ?? item.kind}</span>
              {' — '}
              {item.summary}
            </li>
          ))}
        </ul>
      )}
      <button type="button" className="tkey refresh" onClick={() => void load()} disabled={loading}>
        REFRESH
      </button>
    </section>
  );
};

export default ExplorationSuggestionPanel;
