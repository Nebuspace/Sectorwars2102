import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../../utils/auth';
import { useToast, useConfirm } from '../../contexts/ToastContext';
import './message-moderation.css';

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

interface FlaggedMessagesResponse {
  messages: FlaggedMessage[];
  total: number;
  page: number;
  limit: number;
  pages: number;
}

interface ActiveSender {
  player_id: string;
  message_count: number;
}

interface MessageStats {
  total_messages: number;
  messages_today: number;
  messages_this_week: number;
  flagged_messages: number;
  most_active_senders: ActiveSender[];
}

type ModerationAction = 'delete' | 'unflag';

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

const MessageModeration: React.FC = () => {
  const toast = useToast();
  const confirm = useConfirm();

  const [messages, setMessages] = useState<FlaggedMessage[]>([]);
  const [stats, setStats] = useState<MessageStats | null>(null);
  const [statsError, setStatsError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actingId, setActingId] = useState<string | null>(null);

  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalFlagged, setTotalFlagged] = useState(0);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    setStatsError(null);

    const [flaggedResult, statsResult] = await Promise.allSettled([
      api.get<FlaggedMessagesResponse>(
        `/api/v1/admin/messages/flagged?page=${page}`,
      ),
      api.get<MessageStats>('/api/v1/admin/messages/stats'),
    ]);

    if (flaggedResult.status === 'fulfilled') {
      const data = flaggedResult.value.data;
      setMessages(data.messages ?? []);
      setTotalFlagged(data.total ?? 0);
      setTotalPages(data.pages && data.pages > 0 ? data.pages : 1);
    } else {
      console.error('Failed to load flagged messages:', flaggedResult.reason);
      setMessages([]);
      setTotalFlagged(0);
      setTotalPages(1);
      setError('Failed to load the flagged-message review queue.');
    }

    if (statsResult.status === 'fulfilled') {
      setStats(statsResult.value.data);
    } else {
      console.error('Failed to load message stats:', statsResult.reason);
      setStats(null);
      setStatsError('Statistics are currently unavailable.');
    }

    setLoading(false);
  }, [page]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

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
        await loadData();
      } catch (err) {
        console.error(`Failed to ${action} message:`, err);
        toast.error(
          isDestructive
            ? 'Failed to delete the message.'
            : 'Failed to clear the flag.',
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
          Review flagged player communications and act on reports across the galaxy.
        </p>
      </header>

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

      {/* Review queue */}
      <section className="msgmod-section">
        <div className="msgmod-section-head">
          <h2>Flagged Review Queue</h2>
          <div className="msgmod-section-actions">
            <span className="msgmod-count">
              {totalFlagged.toLocaleString()} flagged
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

        {loading && messages.length === 0 && !error ? (
          <div className="msgmod-empty">Loading flagged messages…</div>
        ) : null}

        {!loading && !error && messages.length === 0 ? (
          <div className="msgmod-empty">No flagged messages.</div>
        ) : null}

        {messages.length > 0 && (
          <div className="msgmod-table-wrap">
            <table className="msgmod-table">
              <thead>
                <tr>
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
                          disabled={actingId === message.id}
                          onClick={() => void moderate(message, 'unflag')}
                        >
                          Clear Flag
                        </button>
                        <button
                          type="button"
                          className="msgmod-btn msgmod-btn-danger"
                          disabled={actingId === message.id}
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
                    <td className="msgmod-sender">{sender.player_id}</td>
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
