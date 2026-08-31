/**
 * LEG-397 — ARIA Tier-1 memory journal (owner JWT read path).
 * Consumes tip GET /api/v1/ai/memories (+ optional /ai/data-index for labels).
 * No reset/export invent — tip lacks those routes.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ariaMemoryAPI,
  type AriaDataStream,
  type AriaMemory,
} from '../../services/api';
import './memory-journal.css';

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

/**
 * Surface gameserver detail when ARIA memory recall fails; network collapse
 * (fetch TypeError) is not GS copy — use the stable fallback (LEG-3072 Soft-ORDER).
 */
export function formatAriaMemoryLoadError(err: unknown): string {
  const fallback = 'Failed to load memories';
  // Network collapse (fetch TypeError) is not gameserver copy — use the fallback.
  if (err instanceof TypeError) return fallback;

  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  const hasServerDetail =
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim());

  if (status === 503 || status === 500) {
    if (hasServerDetail) return message!;
    return fallback;
  }

  if (hasServerDetail) return message!;
  return fallback;
}

const contentPreview = (content: Record<string, unknown>): string => {
  try {
    return JSON.stringify(content);
  } catch {
    return String(content);
  }
};

const MemoryJournalPanel: React.FC = () => {
  const [memories, setMemories] = useState<AriaMemory[]>([]);
  const [streams, setStreams] = useState<AriaDataStream[]>([]);
  const [filterType, setFilterType] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const labelFor = useCallback(
    (memoryType: string): string => {
      const hit = streams.find((s) => s.key === memoryType);
      return hit?.display_name ?? memoryType;
    },
    [streams],
  );

  const loadMemories = useCallback(async (memoryType?: string) => {
    setLoading(true);
    setError(null);
    try {
      const rows = await ariaMemoryAPI.getMemories({
        memoryType: memoryType || undefined,
        limit: 50,
      });
      setMemories(Array.isArray(rows) ? rows : []);
    } catch (err) {
      setMemories([]);
      setError(formatAriaMemoryLoadError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const catalog = await ariaMemoryAPI.getDataIndex();
        if (!cancelled && Array.isArray(catalog)) setStreams(catalog);
      } catch {
        // Optional catalog — journal still works with raw memory_type labels.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    void loadMemories(filterType || undefined);
  }, [filterType, loadMemories]);

  const filterOptions = useMemo(() => {
    const fromIndex = streams
      .filter((s) => s.transparency_visible !== false)
      .map((s) => ({ key: s.key, label: s.display_name || s.key }));
    if (fromIndex.length > 0) return fromIndex;
    const keys = Array.from(new Set(memories.map((m) => m.memory_type))).sort();
    return keys.map((key) => ({ key, label: key }));
  }, [streams, memories]);

  return (
    <section className="aria-memory-journal" aria-label="ARIA memory journal">
      <div className="aria-memory-journal-toolbar">
        <label className="aria-memory-journal-filter-label" htmlFor="aria-memory-type-filter">
          Stream
        </label>
        <select
          id="aria-memory-type-filter"
          className="aria-memory-journal-filter"
          value={filterType}
          onChange={(e) => setFilterType(e.target.value)}
          aria-label="Filter memories by stream type"
        >
          <option value="">All streams</option>
          {filterOptions.map((opt) => (
            <option key={opt.key} value={opt.key}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      <div className="aria-memory-journal-status" aria-live="polite">
        {loading && <p className="aria-memory-journal-loading">Loading memories…</p>}
        {!loading && error && (
          <p className="aria-memory-journal-error" role="alert">
            {error}
          </p>
        )}
        {!loading && !error && memories.length === 0 && (
          <p className="aria-memory-journal-empty">No memories recorded yet.</p>
        )}
      </div>

      {!loading && !error && memories.length > 0 && (
        <ul className="aria-memory-journal-list" role="list">
          {memories.map((mem) => (
            <li key={mem.id} className="aria-memory-journal-item">
              <div className="aria-memory-journal-item-meta">
                <span className="aria-memory-journal-type">{labelFor(mem.memory_type)}</span>
                {mem.created_at ? (
                  <time dateTime={mem.created_at}>{mem.created_at}</time>
                ) : null}
                <span className="aria-memory-journal-scores">
                  imp {mem.importance_score.toFixed(2)} · conf {mem.confidence_level.toFixed(2)}
                </span>
              </div>
              <pre className="aria-memory-journal-content">{contentPreview(mem.content)}</pre>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
};

export default MemoryJournalPanel;
