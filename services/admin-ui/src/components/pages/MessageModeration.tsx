import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../utils/auth';
import { formatAdminApiError } from '../../utils/adminApiError';
import { useToast, useConfirm } from '../../contexts/ToastContext';
import { useWebSocket } from '../../contexts/WebSocketContext';
import './message-moderation.css';

/** GS `flagged_message_alert` payload (websocket maps type → `flagged:message:alert`). */
interface FlaggedMessageAlert {
  type?: string;
  message_id?: string;
  flagged_by_name?: string;
  reason?: string;
  message_preview?: string;
}

/**
 * Message Moderation
 *
 * Review queue for flagged player-to-player / team messages plus the messaging
 * system's statistics summary. Every moderation action calls the real backend
 * moderation endpoint and is guarded by an in-shell confirm dialog + toast
 * result (no native alert/confirm). No mock data — honest empty/error states.
 *
 * Endpoints (all under settings.API_V1_STR === "/api/v1"):
 *   GET  /api/v1/admin/messages/flagged?page=N   -> FlaggedMessagesResponse
 *   GET  /api/v1/admin/messages/stats            -> MessageStats
 *   POST /api/v1/admin/messages/{id}/moderate    -> { success: boolean }
 *   POST /api/v1/admin/messages/bulk-moderate    -> BulkModerateResponse (LEG-270 / LEG-266)
 *   GET  /api/v1/admin/beacons/flagged?page=N    -> FlaggedBeaconsResponse
 *   POST /api/v1/admin/beacons/{id}/clear-flag   -> { success, flagged, ... }
 *   POST /api/v1/admin/beacons/{id}/confirm-abuse -> { success, removed, deployer_player_id, trust_before, trust_after, trust_dock, aria_violation_count }
 */

interface FlaggedMessage {
  id: string;
  sender_id: string;
  recipient_id: string | null;
  team_id: string | null;
  subject: string | null;
  content?: string;
  sent_at: string | null;
  read_at: string | null;
  message_type: string;
  priority: string;
  thread_id: string | null;
  reply_to_id: string | null;
  flagged: boolean;
  is_read: boolean;
  sender_name?: string;
}

interface FlaggedBeacon {
  id: string;
  region_id: string;
  sector_id: number;
  deployer_player_id: string | null;
  deployer_nickname: string | null;
  message: string;
  preview: string;
  deployed_at: string | null;
  flagged: boolean;
}

interface ConfirmAbuseResponse {
  success: boolean;
  removed: boolean;
  deployer_player_id: string;
  trust_before: number;
  trust_after: number;
  trust_dock: number;
  aria_violation_count: number;
}

interface FlaggedBeaconsResponse {
  beacons: FlaggedBeacon[];
  total: number;
  page: number;
  limit: number;
  pages: number;
}

interface FlaggedMessagesResponse {
  messages: FlaggedMessage[];
  total: number;
  page: number;
  limit: number;
  pages: number;
}

interface ActiveSender {
  player_id: string;
  nickname?: string | null;
  message_count: number;
}

interface MessageStats {
  total_messages: number;
  messages_today: number;
  messages_this_week: number;
  flagged_messages: number;
  most_active_senders: ActiveSender[];
}

/** LEG-266 Accepted contract (PR #711) — mass message moderation. */
interface BulkModerateItemResult {
  message_id: string;
  success: boolean;
  detail?: string | null;
}

interface BulkModerateResponse {
  action: string;
  succeeded: number;
  failed: number;
  results: BulkModerateItemResult[];
}

type ModerationAction = 'delete' | 'unflag';
type CanonModerationAction = 'accept' | 'redact' | 'block';

interface CanonModerationResponse {
  success: boolean;
  action: string;
  message_id: string;
  rep_delta: number;
  sender_notified: boolean;
  block_count_30d: number;
  escalation_audit_logged: boolean;
}

const formatTimestamp = (value: string | null): string => {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
};

const truncate = (value: string, max = 280): string =>
  value.length > max ? `${value.slice(0, max)}…` : value;

const recipientLabel = (message: FlaggedMessage): string => {
  if (message.team_id) return `Team ${message.team_id}`;
  if (message.recipient_id) return message.recipient_id;
  return '—';
};

// NO-CANON (flagged to DECISIONS): fallback display when a sender's nickname
// is missing/null — a truncated UUID rather than 'Unknown', so the row still
// carries a stable, at-a-glance identifier an admin can search on.
const senderLabel = (playerId: string, nickname?: string | null): string =>
  nickname ?? `${playerId.slice(0, 8)}…`;

const LIVE_REFRESH_DEBOUNCE_MS = 400;

const MessageModeration: React.FC = () => {
  const toast = useToast();
  const confirm = useConfirm();
  const { isConnected, subscribe } = useWebSocket();
  const liveRefreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [messages, setMessages] = useState<FlaggedMessage[]>([]);
  const [beacons, setBeacons] = useState<FlaggedBeacon[]>([]);
  const [stats, setStats] = useState<MessageStats | null>(null);
  const [statsError, setStatsError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [beaconError, setBeaconError] = useState<string | null>(null);
  const [actingId, setActingId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [bulkActing, setBulkActing] = useState(false);
  const [bulkFailures, setBulkFailures] = useState<BulkModerateItemResult[]>(
    [],
  );

  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalFlagged, setTotalFlagged] = useState(0);
  const [beaconPage, setBeaconPage] = useState(1);
  const [beaconTotalPages, setBeaconTotalPages] = useState(1);
  const [totalFlaggedBeacons, setTotalFlaggedBeacons] = useState(0);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    setBeaconError(null);
    setStatsError(null);

    const [flaggedResult, statsResult, beaconResult] = await Promise.allSettled([
      api.get<FlaggedMessagesResponse>(
        `/api/v1/admin/messages/flagged?page=${page}`,
      ),
      api.get<MessageStats>('/api/v1/admin/messages/stats'),
      api.get<FlaggedBeaconsResponse>(
        `/api/v1/admin/beacons/flagged?page=${beaconPage}`,
      ),
    ]);

    if (flaggedResult.status === 'fulfilled') {
      const data = flaggedResult.value.data;
      setMessages(data.messages ?? []);
      setTotalFlagged(data.total ?? 0);
      setTotalPages(data.pages && data.pages > 0 ? data.pages : 1);
      setSelectedIds([]);
    } else {
      console.error('Failed to load flagged messages:', flaggedResult.reason);
      setMessages([]);
      setTotalFlagged(0);
      setTotalPages(1);
      setSelectedIds([]);
      setError(
        formatAdminApiError(flaggedResult.reason, {
          fallback: 'Failed to load the flagged-message review queue.',
          scopeHint: 'admin messaging moderation scopes required',
        })
      );
    }

    if (statsResult.status === 'fulfilled') {
      setStats(statsResult.value.data);
    } else {
      console.error('Failed to load message stats:', statsResult.reason);
      setStats(null);
      setStatsError(
        formatAdminApiError(statsResult.reason, {
          fallback: 'Statistics are currently unavailable.',
          scopeHint: 'admin messaging statistics scope required',
        })
      );
    }

    if (beaconResult.status === 'fulfilled') {
      const data = beaconResult.value.data;
      setBeacons(data.beacons ?? []);
      setTotalFlaggedBeacons(data.total ?? 0);
      setBeaconTotalPages(data.pages && data.pages > 0 ? data.pages : 1);
    } else {
      console.error('Failed to load flagged beacons:', beaconResult.reason);
      setBeacons([]);
      setTotalFlaggedBeacons(0);
      setBeaconTotalPages(1);
      setBeaconError(
        formatAdminApiError(beaconResult.reason, {
          fallback: 'Failed to load the flagged-beacon review queue.',
          scopeHint: 'admin beacon moderation scopes required',
        })
      );
    }

    setLoading(false);
  }, [page, beaconPage]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  // LEG-414: live-refresh review queue when GS broadcasts flagged_message_alert.
  useEffect(() => {
    const handleFlaggedAlert = (data: FlaggedMessageAlert) => {
      const who = data.flagged_by_name?.trim() || 'a player';
      const reason = data.reason?.trim();
      const preview = data.message_preview?.trim();
      const detail = reason
        ? `${who}: ${truncate(reason, 120)}`
        : preview
          ? `${who}: ${truncate(preview, 120)}`
          : `${who} flagged a message`;
      toast.info(`New flag — ${detail}. Refreshing queue…`);

      if (liveRefreshTimer.current) {
        clearTimeout(liveRefreshTimer.current);
      }
      liveRefreshTimer.current = setTimeout(() => {
        liveRefreshTimer.current = null;
        void loadData();
      }, LIVE_REFRESH_DEBOUNCE_MS);
    };

    const unsubscribe = subscribe('flagged:message:alert', handleFlaggedAlert);
    return () => {
      unsubscribe();
      if (liveRefreshTimer.current) {
        clearTimeout(liveRefreshTimer.current);
        liveRefreshTimer.current = null;
      }
    };
  }, [subscribe, loadData, toast]);

  const moderate = useCallback(
    async (message: FlaggedMessage, action: ModerationAction) => {
      const isDestructive = action === 'delete';
      const confirmed = await confirm({
        title: isDestructive ? 'Delete Message' : 'Clear Flag',
        message: isDestructive
          ? 'Permanently delete this flagged message? This action cannot be undone.'
          : 'Clear the flag on this message and remove it from the review queue?',
        confirmLabel: isDestructive ? 'Delete' : 'Clear Flag',
        danger: isDestructive,
      });
      if (!confirmed) return;

      setActingId(message.id);
      try {
        await api.post<{ success: boolean }>(
          `/api/v1/admin/messages/${message.id}/moderate`,
          { action },
        );
        toast.success(
          isDestructive ? 'Message deleted.' : 'Flag cleared.',
        );
        // Remove the row locally for immediate feedback, then refresh totals.
        setMessages((current) => current.filter((m) => m.id !== message.id));
        setSelectedIds((current) => current.filter((id) => id !== message.id));
        await loadData();
      } catch (err) {
        console.error(`Failed to ${action} message:`, err);
        toast.error(
          formatAdminApiError(err, {
            fallback: isDestructive
              ? 'Failed to delete the message'
              : 'Failed to clear the flag',
            scopeHint: 'admin.messages.moderate scope required for message moderation',
          }),
        );
      } finally {
        setActingId(null);
      }
    },
    [confirm, toast, loadData],
  );

  /** LEG-1579: tip GS canon paths accept/redact/block (distinct from delete/unflag). */
  const canonModerate = useCallback(
    async (message: FlaggedMessage, action: CanonModerationAction) => {
      const titles: Record<CanonModerationAction, string> = {
        accept: 'Accept Flag',
        redact: 'Redact Message',
        block: 'Block Message',
      };
      const bodies: Record<CanonModerationAction, string> = {
        accept:
          'Clear the flag and leave the message visible? No reputation penalty.',
        redact:
          'Replace the message body with [Moderated], notify the sender, and apply −50 personal reputation?',
        block:
          'Hide the message from player reads, notify the sender, and apply −100 personal reputation? (2+ blocks / 30d logs an audit escalation only — no account_review invent.)',
      };
      const confirmed = await confirm({
        title: titles[action],
        message: bodies[action],
        confirmLabel:
          action === 'accept' ? 'Accept' : action === 'redact' ? 'Redact' : 'Block',
        danger: action !== 'accept',
      });
      if (!confirmed) return;

      setActingId(message.id);
      try {
        const { data } = await api.post<CanonModerationResponse>(
          `/api/v1/admin/moderation/messages/${message.id}/${action}`,
          {},
        );
        const rep =
          typeof data?.rep_delta === 'number' && data.rep_delta !== 0
            ? ` Reputation Δ ${data.rep_delta}.`
            : '';
        const escalate =
          data?.escalation_audit_logged
            ? ' Escalation audit logged (2+ blocks/30d).'
            : '';
        toast.success(
          action === 'accept'
            ? `Flag accepted.${rep}`
            : action === 'redact'
              ? `Message redacted.${rep}${escalate}`
              : `Message blocked.${rep}${escalate}`,
        );
        if (data?.block_count_30d && data.block_count_30d >= 2 && !data.escalation_audit_logged) {
          toast.info(`Sender block count (30d): ${data.block_count_30d}.`);
        }
        setMessages((current) => current.filter((m) => m.id !== message.id));
        setSelectedIds((current) => current.filter((id) => id !== message.id));
        await loadData();
      } catch (err) {
        console.error(`Failed to ${action} message:`, err);
        toast.error(
          formatAdminApiError(err, {
            fallback: `Failed to ${action} the message`,
            scopeHint: 'admin.security.act scope required for canon moderation',
          }),
        );
      } finally {
        setActingId(null);
      }
    },
    [confirm, toast, loadData],
  );

  const toggleSelected = useCallback((messageId: string) => {
    setSelectedIds((current) =>
      current.includes(messageId)
        ? current.filter((id) => id !== messageId)
        : [...current, messageId],
    );
  }, []);

  const allPageSelected =
    messages.length > 0 && messages.every((m) => selectedIds.includes(m.id));

  const toggleSelectAllPage = useCallback(() => {
    setSelectedIds((current) => {
      const pageIds = messages.map((m) => m.id);
      const allSelected =
        pageIds.length > 0 && pageIds.every((id) => current.includes(id));
      if (allSelected) {
        return current.filter((id) => !pageIds.includes(id));
      }
      const merged = new Set([...current, ...pageIds]);
      return Array.from(merged);
    });
  }, [messages]);

  const bulkModerate = useCallback(
    async (action: ModerationAction) => {
      const ids = selectedIds.filter((id) =>
        messages.some((m) => m.id === id),
      );
      if (ids.length === 0) return;

      const isDestructive = action === 'delete';
      const confirmed = await confirm({
        title: isDestructive ? 'Bulk Delete Messages' : 'Bulk Clear Flags',
        message: isDestructive
          ? `Permanently delete ${ids.length} flagged message${ids.length === 1 ? '' : 's'}? This action cannot be undone.`
          : `Clear the flag on ${ids.length} message${ids.length === 1 ? '' : 's'} and remove them from the review queue?`,
        confirmLabel: isDestructive ? 'Delete selected' : 'Clear flags',
        danger: isDestructive,
      });
      if (!confirmed) return;

      setBulkActing(true);
      setBulkFailures([]);
      try {
        const res = await api.post<BulkModerateResponse>(
          '/api/v1/admin/messages/bulk-moderate',
          { message_ids: ids, action },
        );
        const payload = res.data;
        const succeededIds = (payload.results ?? [])
          .filter((r) => r.success)
          .map((r) => r.message_id);
        const failed = (payload.results ?? []).filter((r) => !r.success);

        if (succeededIds.length > 0) {
          setMessages((current) =>
            current.filter((m) => !succeededIds.includes(m.id)),
          );
          setSelectedIds((current) =>
            current.filter((id) => !succeededIds.includes(id)),
          );
        }

        await loadData();

        if (payload.failed > 0 || failed.length > 0) {
          setBulkFailures(failed);
          toast.warning(
            `Bulk ${action}: ${payload.succeeded} succeeded, ${payload.failed} failed.`,
          );
        } else {
          setBulkFailures([]);
          toast.success(
            isDestructive
              ? `Deleted ${payload.succeeded} message${payload.succeeded === 1 ? '' : 's'}.`
              : `Cleared flags on ${payload.succeeded} message${payload.succeeded === 1 ? '' : 's'}.`,
          );
        }
      } catch (err) {
        console.error(`Failed to bulk ${action} messages:`, err);
        toast.error(
          isDestructive
            ? 'Failed to bulk-delete messages.'
            : 'Failed to bulk-clear flags.',
        );
      } finally {
        setBulkActing(false);
      }
    },
    [selectedIds, messages, confirm, toast, loadData],
  );

  const clearBeaconFlag = useCallback(
    async (beacon: FlaggedBeacon) => {
      const confirmed = await confirm({
        title: 'Clear Beacon Flag',
        message:
          'Clear the report flag so this sector beacon reappears for players?',
        confirmLabel: 'Clear Flag',
        danger: false,
      });
      if (!confirmed) return;

      setActingId(beacon.id);
      try {
        await api.post<{ success: boolean }>(
          `/api/v1/admin/beacons/${beacon.id}/clear-flag`,
        );
        toast.success('Beacon flag cleared.');
        setBeacons((current) => current.filter((b) => b.id !== beacon.id));
        await loadData();
      } catch (err) {
        console.error('Failed to clear beacon flag:', err);
        toast.error(
          formatAdminApiError(err, {
            fallback: 'Failed to clear the beacon flag',
            scopeHint: 'admin.beacons.moderate scope required for beacon moderation',
          }),
        );
      } finally {
        setActingId(null);
      }
    },
    [confirm, toast, loadData],
  );

  const confirmBeaconAbuse = useCallback(
    async (beacon: FlaggedBeacon) => {
      const confirmed = await confirm({
        title: 'Confirm Abuse',
        message:
          'Confirm this beacon as abusive? This docks the deployer\'s trust score, ' +
          'counts as a violation, and permanently removes the beacon. This action ' +
          'cannot be undone — use Clear Flag instead for false reports.',
        confirmLabel: 'Confirm Abuse',
        danger: true,
      });
      if (!confirmed) return;

      setActingId(beacon.id);
      try {
        const res = await api.post<ConfirmAbuseResponse>(
          `/api/v1/admin/beacons/${beacon.id}/confirm-abuse`,
        );
        toast.success(
          `Beacon removed. Deployer trust ${res.data.trust_before.toFixed(2)} → ` +
          `${res.data.trust_after.toFixed(2)} (violation #${res.data.aria_violation_count}).`,
        );
        setBeacons((current) => current.filter((b) => b.id !== beacon.id));
        await loadData();
      } catch (err) {
        console.error('Failed to confirm beacon abuse:', err);
        toast.error(
          formatAdminApiError(err, {
            fallback: 'Failed to confirm abuse for this beacon',
            scopeHint: 'admin.beacons.moderate scope required for beacon abuse confirmation',
          }),
        );
      } finally {
        setActingId(null);
      }
    },
    [confirm, toast, loadData],
  );

  return (
    <div className="message-moderation">
      <header className="msgmod-header">
        <h1>Message Moderation</h1>
        <p className="msgmod-subtitle">
          Review flagged player communications and sector message beacons.
        </p>
      </header>

      {/* Review queue */}
      <section className="msgmod-section">
        <div className="msgmod-section-head">
          <h2>Flagged Review Queue</h2>
          <div className="msgmod-section-actions">
            <span className="msgmod-count">
              {totalFlagged.toLocaleString()} flagged
            </span>
            {!isConnected ? (
              <span className="msgmod-live-demotion" role="status">
                Live updates unavailable — use Refresh
              </span>
            ) : null}
            <button
              type="button"
              className="msgmod-btn msgmod-btn-secondary"
              onClick={() => void loadData()}
              disabled={loading}
            >
              Refresh
            </button>
          </div>
        </div>

        {error && (
          <div className="msgmod-error">
            <span>{error}</span>
            <button
              type="button"
              className="msgmod-btn msgmod-btn-secondary"
              onClick={() => void loadData()}
            >
              Retry
            </button>
          </div>
        )}

        {bulkFailures.length > 0 && (
          <div
            className="msgmod-bulk-failures"
            role="status"
            data-testid="bulk-partial-failures"
          >
            <strong>Bulk moderation partial failures</strong>
            <ul>
              {bulkFailures.map((f) => (
                <li key={f.message_id}>
                  {f.message_id}
                  {f.detail ? `: ${f.detail}` : ' — failed'}
                </li>
              ))}
            </ul>
          </div>
        )}

        {loading && messages.length === 0 && !error ? (
          <div className="msgmod-empty">Loading flagged messages…</div>
        ) : null}

        {!loading && !error && messages.length === 0 ? (
          <div className="msgmod-empty">No flagged messages.</div>
        ) : null}

        {messages.length > 0 && selectedIds.length > 0 && (
          <div className="msgmod-bulk-bar" data-testid="bulk-action-bar">
            <span className="msgmod-bulk-count">
              {selectedIds.length} selected
            </span>
            <button
              type="button"
              className="msgmod-btn msgmod-btn-secondary"
              disabled={bulkActing || actingId !== null}
              onClick={() => void bulkModerate('unflag')}
            >
              Bulk Clear Flag
            </button>
            <button
              type="button"
              className="msgmod-btn msgmod-btn-danger"
              disabled={bulkActing || actingId !== null}
              onClick={() => void bulkModerate('delete')}
            >
              Bulk Delete
            </button>
          </div>
        )}

        {messages.length > 0 && (
          <div className="msgmod-table-wrap">
            <table className="msgmod-table">
              <thead>
                <tr>
                  <th className="msgmod-select-col">
                    <input
                      type="checkbox"
                      aria-label="Select all messages on this page"
                      checked={allPageSelected}
                      disabled={bulkActing}
                      onChange={toggleSelectAllPage}
                    />
                  </th>
                  <th>Sender</th>
                  <th>Recipient</th>
                  <th>Content</th>
                  <th>Status</th>
                  <th>Sent</th>
                  <th className="msgmod-actions-col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {messages.map((message) => (
                  <tr key={message.id}>
                    <td className="msgmod-select-col">
                      <input
                        type="checkbox"
                        aria-label={`Select message ${message.id}`}
                        checked={selectedIds.includes(message.id)}
                        disabled={bulkActing}
                        onChange={() => toggleSelected(message.id)}
                      />
                    </td>
                    <td className="msgmod-sender">
                      {message.sender_name ?? message.sender_id}
                    </td>
                    <td className="msgmod-recipient">
                      {recipientLabel(message)}
                    </td>
                    <td className="msgmod-content">
                      {message.subject && (
                        <span className="msgmod-msg-subject">
                          {message.subject}
                        </span>
                      )}
                      <span className="msgmod-msg-body">
                        {message.content
                          ? truncate(message.content)
                          : '(no content)'}
                      </span>
                    </td>
                    <td className="msgmod-reason">
                      {message.flagged ? (
                        <span className="msgmod-flag-badge">Flagged</span>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td className="msgmod-sent">
                      {formatTimestamp(message.sent_at)}
                    </td>
                    <td className="msgmod-actions-col">
                      <div className="msgmod-row-actions">
                        <button
                          type="button"
                          className="msgmod-btn msgmod-btn-secondary"
                          disabled={
                            actingId === message.id || bulkActing
                          }
                          onClick={() => void canonModerate(message, 'accept')}
                        >
                          Accept
                        </button>
                        <button
                          type="button"
                          className="msgmod-btn msgmod-btn-secondary"
                          disabled={
                            actingId === message.id || bulkActing
                          }
                          onClick={() => void canonModerate(message, 'redact')}
                        >
                          Redact
                        </button>
                        <button
                          type="button"
                          className="msgmod-btn msgmod-btn-danger"
                          disabled={
                            actingId === message.id || bulkActing
                          }
                          onClick={() => void canonModerate(message, 'block')}
                        >
                          Block
                        </button>
                        <button
                          type="button"
                          className="msgmod-btn msgmod-btn-secondary"
                          disabled={
                            actingId === message.id || bulkActing
                          }
                          onClick={() => void moderate(message, 'unflag')}
                        >
                          Clear Flag
                        </button>
                        <button
                          type="button"
                          className="msgmod-btn msgmod-btn-danger"
                          disabled={
                            actingId === message.id || bulkActing
                          }
                          onClick={() => void moderate(message, 'delete')}
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {totalPages > 1 && (
          <div className="msgmod-pagination">
            <button
              type="button"
              className="msgmod-btn msgmod-btn-secondary"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1 || loading}
            >
              Previous
            </button>
            <span className="msgmod-page-indicator">
              Page {page} of {totalPages}
            </span>
            <button
              type="button"
              className="msgmod-btn msgmod-btn-secondary"
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages || loading}
            >
              Next
            </button>
          </div>
        )}
      </section>

      {/* Flagged sector beacons (WO-BEACON-ADMIN-CLEAR-FLAG) */}
      <section className="msgmod-section">
        <div className="msgmod-section-head">
          <h2>Flagged Sector Beacons</h2>
          <div className="msgmod-section-actions">
            <span className="msgmod-count">
              {totalFlaggedBeacons.toLocaleString()} flagged
            </span>
            <button
              type="button"
              className="msgmod-btn msgmod-btn-secondary"
              onClick={() => void loadData()}
              disabled={loading}
            >
              Refresh
            </button>
          </div>
        </div>

        {beaconError && (
          <div className="msgmod-error">
            <span>{beaconError}</span>
            <button
              type="button"
              className="msgmod-btn msgmod-btn-secondary"
              onClick={() => void loadData()}
            >
              Retry
            </button>
          </div>
        )}

        {!loading && !beaconError && beacons.length === 0 ? (
          <div className="msgmod-empty">No flagged sector beacons.</div>
        ) : null}

        {beacons.length > 0 && (
          <div className="msgmod-table-wrap">
            <table className="msgmod-table">
              <thead>
                <tr>
                  <th>Deployer</th>
                  <th>Sector</th>
                  <th>Message</th>
                  <th>Deployed</th>
                  <th className="msgmod-actions-col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {beacons.map((beacon) => (
                  <tr key={beacon.id}>
                    <td className="msgmod-sender">
                      {beacon.deployer_nickname
                        ?? beacon.deployer_player_id
                        ?? '—'}
                    </td>
                    <td className="msgmod-recipient">
                      {beacon.sector_id}
                    </td>
                    <td className="msgmod-content">
                      <span className="msgmod-msg-body">
                        {truncate(beacon.message || beacon.preview || '')}
                      </span>
                    </td>
                    <td className="msgmod-sent">
                      {formatTimestamp(beacon.deployed_at)}
                    </td>
                    <td className="msgmod-actions-col">
                      <div className="msgmod-row-actions">
                        <button
                          type="button"
                          className="msgmod-btn msgmod-btn-secondary"
                          disabled={actingId === beacon.id}
                          onClick={() => void clearBeaconFlag(beacon)}
                        >
                          Clear Flag
                        </button>
                        <button
                          type="button"
                          className="msgmod-btn msgmod-btn-danger"
                          disabled={actingId === beacon.id}
                          onClick={() => void confirmBeaconAbuse(beacon)}
                        >
                          Confirm Abuse
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {beaconTotalPages > 1 && (
          <div className="msgmod-pagination">
            <button
              type="button"
              className="msgmod-btn msgmod-btn-secondary"
              onClick={() => setBeaconPage((p) => Math.max(1, p - 1))}
              disabled={beaconPage <= 1 || loading}
            >
              Previous
            </button>
            <span className="msgmod-page-indicator">
              Page {beaconPage} of {beaconTotalPages}
            </span>
            <button
              type="button"
              className="msgmod-btn msgmod-btn-secondary"
              onClick={() =>
                setBeaconPage((p) => Math.min(beaconTotalPages, p + 1))
              }
              disabled={beaconPage >= beaconTotalPages || loading}
            >
              Next
            </button>
          </div>
        )}
      </section>

      {/* Statistics summary */}
      <section className="msgmod-section">
        {statsError && <div className="msgmod-inline-error">{statsError}</div>}
        {stats && (
          <div className="msgmod-stats-grid">
            <div className="msgmod-stat-card">
              <span className="msgmod-stat-label">Total Messages</span>
              <span className="msgmod-stat-value">
                {stats.total_messages.toLocaleString()}
              </span>
            </div>
            <div className="msgmod-stat-card">
              <span className="msgmod-stat-label">Today</span>
              <span className="msgmod-stat-value">
                {stats.messages_today.toLocaleString()}
              </span>
            </div>
            <div className="msgmod-stat-card">
              <span className="msgmod-stat-label">This Week</span>
              <span className="msgmod-stat-value">
                {stats.messages_this_week.toLocaleString()}
              </span>
            </div>
            <div className="msgmod-stat-card msgmod-stat-flagged">
              <span className="msgmod-stat-label">Flagged</span>
              <span className="msgmod-stat-value">
                {stats.flagged_messages.toLocaleString()}
              </span>
            </div>
          </div>
        )}
      </section>

      {/* Most active senders (from stats) */}
      {stats && stats.most_active_senders.length > 0 && (
        <section className="msgmod-section">
          <div className="msgmod-section-head">
            <h2>Most Active Senders</h2>
          </div>
          <div className="msgmod-table-wrap">
            <table className="msgmod-table">
              <thead>
                <tr>
                  <th>Player</th>
                  <th>Messages Sent</th>
                </tr>
              </thead>
              <tbody>
                {stats.most_active_senders.map((sender) => (
                  <tr key={sender.player_id}>
                    <td className="msgmod-sender" title={sender.player_id}>
                      {senderLabel(sender.player_id, sender.nickname)}
                    </td>
                    <td>{sender.message_count.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
};

export default MessageModeration;
