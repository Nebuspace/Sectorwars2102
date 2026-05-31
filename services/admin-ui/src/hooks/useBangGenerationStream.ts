/**
 * SSE consumer for `GET /admin/galaxy/jobs/{id}/stream`.
 *
 * Browsers can't send a custom Authorization header on `EventSource`, so
 * the JWT is appended as `?token=`. This matches the pattern established
 * by `src/services/websocket.ts` for the admin WebSocket.
 *
 * The hook is intentionally minimal: own a single EventSource for the
 * lifetime of the (jobId, token) pair, buffer text lines into state,
 * track an inferred status, and close cleanly on unmount or when the
 * server signals a terminal status via `event: status`.
 *
 * Reconnect behaviour: native EventSource auto-reconnects on transport
 * errors with a browser-default backoff. We deliberately do NOT add a
 * manual reconnect loop here — a bang generation job is bounded in
 * duration (single-digit minutes worst case), and aggressive client
 * retries would risk duplicating log lines if the server didn't
 * de-duplicate. If the stream errors before the server emits a terminal
 * status the hook surfaces `error` to the UI.
 */
import { useEffect, useRef, useState } from 'react';

import type { BangJobStatus } from '../components/universe/bang/types';
import { useAuth } from '../contexts/AuthContext';

export interface BangStreamState {
  /** Log lines accumulated so far, in arrival order. */
  lines: string[];
  /** Inferred job status from the latest `event: status` frame. */
  status: BangJobStatus;
  /** True between EventSource.OPEN and close/terminal status. */
  isStreaming: boolean;
  /** Transport-level error string, if any. */
  error: string | null;
}

const TERMINAL_STATUSES: ReadonlySet<BangJobStatus> = new Set<BangJobStatus>([
  'COMPLETE',
  'FAILED',
]);

export function useBangGenerationStream(
  jobId: string | null,
): BangStreamState {
  const { token } = useAuth();
  const [lines, setLines] = useState<string[]>([]);
  const [status, setStatus] = useState<BangJobStatus>('PENDING');
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    // Reset state on jobId change.
    setLines([]);
    setStatus('PENDING');
    setIsStreaming(false);
    setError(null);

    if (!jobId || !token) {
      return;
    }

    const url = `/api/v1/admin/galaxy/jobs/${jobId}/stream?token=${encodeURIComponent(token)}`;
    const es = new EventSource(url);
    eventSourceRef.current = es;

    es.onopen = () => {
      setIsStreaming(true);
      setStatus('RUNNING');
    };

    es.onmessage = (evt: MessageEvent<string>) => {
      // Default channel = log line.
      if (evt.data) {
        setLines((prev) => [...prev, evt.data]);
      }
    };

    // Named `status` event — emitted once when the job leaves RUNNING.
    es.addEventListener('status', (evt) => {
      const next = (evt as MessageEvent<string>).data as BangJobStatus;
      setStatus(next);
      if (TERMINAL_STATUSES.has(next)) {
        setIsStreaming(false);
        es.close();
      }
    });

    es.onerror = () => {
      // EventSource will auto-retry on transient errors. Only surface an
      // error if the stream is closed (readyState === CLOSED).
      if (es.readyState === EventSource.CLOSED) {
        setIsStreaming(false);
        setError('SSE stream closed unexpectedly');
      }
    };

    return () => {
      es.close();
      eventSourceRef.current = null;
      setIsStreaming(false);
    };
  }, [jobId, token]);

  return { lines, status, isStreaming, error };
}
