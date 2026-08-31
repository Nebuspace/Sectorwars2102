/**
 * COMMS / CREW — MFD-B COMM ops page.
 *
 * WO-UI2-DECK-RECONCILE: the deck COMMS monitor is retiring (SPACE deck
 * collapses to 3 monitors); its HAILS inbox + composer FOLD UP here so the
 * mailbox has a home before that monitor is removed. MFD-B is present in
 * ALL modes (flight/docked/landed), so COMM now works everywhere — not
 * flight-only like the old deck monitor.
 *
 * Sector contacts merge live WebSocket presence (sectorPlayers, human pilots)
 * with the API sector snapshot (currentSector.players_present, which also
 * carries NPC presence entries) — the same source the (retiring) deck COMMS
 * monitor used. Without the snapshot the page was blind to NPCs, so a sector
 * full of patrolling marshals showed "no contacts". Uplink + unread + crew
 * affiliation come from GameContext/WebSocketContext.
 *
 * Inbox + composer logic (message list, unread-driven refetch, send/reply,
 * recipient sourcing) is ported from the retiring components/comms/
 * CommsMailbox.tsx HAILS mode — same GameContext binding
 * (inboxMessages/refreshInbox/sendPlayerMessage/markMessageRead/deletePlayerMessage), same two
 * recipient sources: HAIL a sector contact (player_id required — NPCs and
 * live-WS-only contacts without a snapshot entry don't get a HAIL button)
 * or REPLY to an inbox message. No manual recipient entry in v1.
 * WO-WIRE-MESSAGE-DELETE: expanded hails expose PURGE → deletePlayerMessage.
 */

import React from 'react';
import { useGame, type PlayerMessage } from '../../../contexts/GameContext';
import { useWebSocket } from '../../../contexts/WebSocketContext';
import { useAuth } from '../../../contexts/AuthContext';
import { messageAPI, teamAPI } from '../../../services/api';
import PlayerNamePlate from '../../common/PlayerNamePlate';
import { MFDPageHeader, MFDPageBody, MFDField, MFDEmpty } from '../atoms';
import './pages-ops.css';

const ACCENT = '#00FF7F';

/** Canon categories (messaging.md). Tip Query reason requires 10–255 chars —
 *  bare `spam`/`other` are too short, so pad while keeping the category token. */
export type FlagCategory = 'harassment' | 'spam' | 'rule_break' | 'other';
export const FLAG_REASON_BY_CATEGORY: Record<FlagCategory, string> = {
  harassment: 'harassment',
  spam: 'spam report',
  rule_break: 'rule_break',
  other: 'other report',
};
const FLAG_CATEGORIES: FlagCategory[] = ['harassment', 'spam', 'rule_break', 'other'];

interface ComposeTarget {
  recipientId: string;
  recipientName: string;
  replyToId?: string;
}

// Compact relative timestamp for the inbox list (CRT-terse) — verbatim from
// CommsMailbox.tsx.
const timeAgo = (iso: string | null): string => {
  if (!iso) return '--';
  const deltaMs = Date.now() - new Date(iso).getTime();
  if (!isFinite(deltaMs)) return '--';
  const mins = Math.floor(deltaMs / 60000);
  if (mins < 1) return 'NOW';
  if (mins < 60) return `${mins}M AGO`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}H AGO`;
  return `${Math.floor(hours / 24)}D AGO`;
};

const contactDisplayName = (contact: any): string =>
  contact.username || contact.name || 'UNKNOWN CONTACT';

type CommsMode = 'hails' | 'threads';

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

/** Transport collapse copy is not gameserver detail (network-collapse densify). */
const isCommsNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed) ||
    /^networkerror$/i.test(trimmed)
  );
};

/**
 * Prefer response.data.detail, else a non-generic Error.message.
 * Network collapse (fetch TypeError / axios transport) is not gameserver copy — return
 * undefined so formatters use their stable fallbacks (LEG-3073 Soft-ORDER).
 */
function serverDetail(err: unknown): string | undefined {
  // Network collapse (fetch TypeError) is not gameserver copy.
  if (err instanceof TypeError) return undefined;

  if (err && typeof err === 'object') {
    const rawDetail = (err as { response?: { data?: { detail?: unknown } } }).response?.data
      ?.detail;
    if (typeof rawDetail === 'string' && rawDetail.trim()) return rawDetail.trim();
  }
  const message = err instanceof Error ? err.message : undefined;
  if (typeof message === 'string' && isCommsNetworkCollapseMessage(message)) return undefined;
  if (
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim())
  ) {
    return message.trim();
  }
  return undefined;
}

/** apiRequest throws Error with `.status`; legacy axios callers may use `.response.data.detail`. */
export function formatCommsFlagError(err: unknown): string {
  const status = httpStatus(err);
  const detail = serverDetail(err);

  if (status === 404) {
    if (detail) return detail;
    return 'Message not found';
  }

  if (status === 403) {
    if (detail) return detail;
    return 'Access denied — you cannot flag transmissions right now.';
  }

  if (status === 429) {
    if (detail) return detail;
    return 'Flag rate limit exceeded — wait a moment and try again.';
  }

  if (detail) return detail;
  return 'Failed to flag transmission';
}

/** Compose send — surface 429 rate-limit + 409 thread-cap refusals (messaging.md). */
export function formatCommsSendError(err: unknown): string {
  const status = httpStatus(err);
  const detail = serverDetail(err);

  if (status === 429) {
    if (detail) return detail;
    return 'Too many messages — limit is 5 per 60s. Wait a moment and try again.';
  }

  if (status === 409) {
    if (detail && detail !== 'thread_limit_exceeded') return detail;
    return 'Thread is full (50 messages) — archive or start a new thread.';
  }

  if (detail) return detail;
  return 'TRANSMISSION FAILED';
}

/** PURGE soft-delete — surface gameserver 404 detail (messages.py). */
export function formatCommsPurgeError(err: unknown): string {
  const status = httpStatus(err);
  const detail = serverDetail(err);

  if (status === 404) {
    if (detail) return detail;
    return 'Message not found';
  }

  if (detail) return detail;
  return 'Failed to purge transmission';
}

/** THREADS tab load — surface gameserver detail on GET /messages/conversations failure. */
export function formatCommsThreadsLoadError(err: unknown): string {
  const status = httpStatus(err);
  const detail = serverDetail(err);

  if (status === 403) {
    if (detail) return detail;
    return 'Access denied — you cannot view threads right now.';
  }

  if (status === 429) {
    if (detail) return detail;
    return 'Thread lookup rate limit exceeded — wait a moment and try again.';
  }

  if (detail) return detail;
  return 'Failed to load threads';
}

const conversationPartyLabel = (msg: PlayerMessage, playerId: string | undefined): string => {
  if (!playerId) return (msg.sender_name || 'UNKNOWN').toUpperCase();
  if (msg.sender_id !== playerId) return (msg.sender_name || 'UNKNOWN').toUpperCase();
  return msg.subject ? `OUT: ${msg.subject}`.toUpperCase() : 'OUTBOUND';
};

const CommsCrewPage: React.FC = () => {
  const {
    playerState,
    currentSector,
    unreadMessageCount,
    inboxMessages,
    refreshInbox,
    sendPlayerMessage,
    markMessageRead,
    deletePlayerMessage,
  } = useGame();
  const { isConnected, sectorPlayers, newMessageSignal } = useWebSocket();
  const { user } = useAuth();

  // Resolve the player's team name for the CREW affiliation line (playerState
  // carries only team_id, not the name). One fetch per team_id change; falls
  // back to "ACTIVE" while loading or if the lookup fails.
  const [teamLabel, setTeamLabel] = React.useState<string | null>(null);
  React.useEffect(() => {
    const teamId = playerState?.team_id;
    if (!teamId) { setTeamLabel(null); return; }
    let cancelled = false;
    teamAPI.getTeam(teamId)
      .then((t: any) => {
        if (cancelled) return;
        const name = t?.name as string | undefined;
        const tag = t?.tag as string | undefined;
        setTeamLabel(name ? (tag ? `[${tag}] ${name}` : name) : null);
      })
      .catch(() => { if (!cancelled) setTeamLabel(null); });
    return () => { cancelled = true; };
  }, [playerState?.team_id]);

  // Merge WS presence + API snapshot, drop self, de-dupe. Mirrors the
  // (retiring) deck COMMS monitor's contact merge: real pilots key on
  // lowercased username (they appear in both sources); NPC entries key on
  // their NPCCharacter id (player_id) since same-named captains must stay
  // distinct and they have no username.
  const contacts = React.useMemo(() => {
    const map = new Map<string, any>();
    const add = (c: any) => {
      if (!c) return;
      const key = c.is_npc
        ? String(c.player_id || c.user_id || c.id || '')
        : String((c.username && c.username.toLowerCase()) || c.user_id || c.id || '');
      if (!key) return;
      const isSelf = playerState && (
        key === String(playerState.id) ||
        (c.username && (playerState as any).username &&
          c.username.toLowerCase() === (playerState as any).username.toLowerCase())
      );
      if (isSelf) return;
      const existing = map.get(key);
      if (!existing) {
        map.set(key, c);
      } else if (!existing.player_id && c.player_id) {
        map.set(key, { ...existing, ...c });
      }
    };
    sectorPlayers.forEach(add);
    ((currentSector as any)?.players_present || []).forEach(add);
    return Array.from(map.values());
  }, [sectorPlayers, currentSector, playerState]);

  // --- Inbox + composer state (ported from CommsMailbox.tsx HAILS mode) ---
  const [expandedId, setExpandedId] = React.useState<string | null>(null);
  const [compose, setCompose] = React.useState<ComposeTarget | null>(null);
  const [composeSubject, setComposeSubject] = React.useState('');
  const [composeContent, setComposeContent] = React.useState('');
  const [isSending, setIsSending] = React.useState(false);
  const [sendError, setSendError] = React.useState<string | null>(null);
  const [sendNotice, setSendNotice] = React.useState<string | null>(null);
  const [deletingId, setDeletingId] = React.useState<string | null>(null);
  const [flagMenuId, setFlagMenuId] = React.useState<string | null>(null);
  const [flaggingId, setFlaggingId] = React.useState<string | null>(null);
  const [flagError, setFlagError] = React.useState<string | null>(null);
  const [flagNotice, setFlagNotice] = React.useState<string | null>(null);

  const [commsMode, setCommsMode] = React.useState<CommsMode>('hails');
  const [conversations, setConversations] = React.useState<PlayerMessage[]>([]);
  const [conversationsLoading, setConversationsLoading] = React.useState(false);
  const [conversationsError, setConversationsError] = React.useState<string | null>(null);
  const [selectedThreadId, setSelectedThreadId] = React.useState<string | null>(null);
  const [expandedThreadMsgId, setExpandedThreadMsgId] = React.useState<string | null>(null);

  // Initial inbox fetch once auth has hydrated, then again on every live
  // new_message notification — the unread badge stays current without a
  // reload. On a hard reload the mount fires while `user` is still null
  // (refreshInbox no-ops); keying on `user?.id` re-runs the effect the
  // instant auth resolves, so the inbox/badge hydrate exactly once.
  // (refreshInbox is recreated each provider render, so it stays out of
  // the dependency list — user identity + the signal are the real triggers.)
  //
  // A burst of arrivals (signal flips several times in quick succession)
  // collapses to a single refetch via a 1.5s trailing debounce.
  const refreshTimer = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  React.useEffect(() => {
    if (!user?.id) return;
    if (refreshTimer.current) clearTimeout(refreshTimer.current);
    refreshTimer.current = setTimeout(() => {
      refreshInbox();
      refreshTimer.current = null;
    }, 1500);
    return () => {
      if (refreshTimer.current) {
        clearTimeout(refreshTimer.current);
        refreshTimer.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newMessageSignal, user?.id]);

  React.useEffect(() => {
    if (commsMode !== 'threads' || !user?.id) return;
    let cancelled = false;
    setConversationsLoading(true);
    setConversationsError(null);
    messageAPI.getConversations(1)
      .then((res: { conversations?: PlayerMessage[] }) => {
        if (cancelled) return;
        setConversations(Array.isArray(res?.conversations) ? res.conversations : []);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setConversations([]);
        setConversationsError(formatCommsThreadsLoadError(err));
      })
      .finally(() => {
        if (!cancelled) setConversationsLoading(false);
      });
    refreshInbox();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [commsMode, user?.id]);

  const threadMessages = React.useMemo(() => {
    if (!selectedThreadId) return [];
    const fromInbox = inboxMessages.filter((m) => m.thread_id === selectedThreadId);
    const preview = conversations.find((c) => c.thread_id === selectedThreadId);
    const merged = new Map<string, PlayerMessage>();
    fromInbox.forEach((m) => merged.set(m.id, m));
    if (preview && !merged.has(preview.id)) merged.set(preview.id, preview);
    return Array.from(merged.values()).sort((a, b) => {
      const ta = a.sent_at ? new Date(a.sent_at).getTime() : 0;
      const tb = b.sent_at ? new Date(b.sent_at).getTime() : 0;
      return ta - tb;
    });
  }, [selectedThreadId, inboxMessages, conversations]);

  const switchCommsMode = (mode: CommsMode) => {
    setCommsMode(mode);
    setExpandedId(null);
    setSelectedThreadId(null);
    setExpandedThreadMsgId(null);
    setFlagMenuId(null);
    setFlagError(null);
    setFlagNotice(null);
  };

  const selectThread = (msg: PlayerMessage) => {
    const tid = msg.thread_id || msg.id;
    setSelectedThreadId(tid);
    setExpandedThreadMsgId(null);
    setFlagMenuId(null);
    setFlagError(null);
    setFlagNotice(null);
  };

  const toggleExpand = (msg: PlayerMessage) => {
    if (expandedId === msg.id) {
      setExpandedId(null);
      setFlagMenuId(null);
      return;
    }
    setExpandedId(msg.id);
    setFlagMenuId(null);
    setFlagError(null);
    setFlagNotice(null);
    if (!msg.is_read) {
      // Reading IS the read receipt — but a failed flag write must not
      // block reading the transmission itself.
      markMessageRead(msg.id).catch(err =>
        console.warn('CommsCrewPage: failed to mark message read:', err)
      );
    }
  };

  const startReply = (msg: PlayerMessage) => {
    setCompose({
      recipientId: msg.sender_id,
      recipientName: msg.sender_name || 'UNKNOWN',
      replyToId: msg.id
    });
    setComposeSubject(
      msg.subject ? (/^re:/i.test(msg.subject) ? msg.subject : `RE: ${msg.subject}`) : ''
    );
    setSendError(null);
    setSendNotice(null);
  };

  const startHail = (contact: any) => {
    if (!contact.player_id) return;
    setCompose({
      recipientId: contact.player_id,
      recipientName: contactDisplayName(contact)
    });
    setComposeSubject('');
    setSendError(null);
    setSendNotice(null);
  };

  const clearCompose = () => {
    setCompose(null);
    setComposeSubject('');
    setComposeContent('');
    setSendError(null);
    setSendNotice(null);
  };

  const handleDelete = async (msg: PlayerMessage) => {
    if (deletingId) return;
    setDeletingId(msg.id);
    setSendError(null);
    try {
      await deletePlayerMessage(msg.id);
      if (expandedId === msg.id) setExpandedId(null);
      if (compose?.replyToId === msg.id) clearCompose();
    } catch (err: unknown) {
      setSendError(formatCommsPurgeError(err));
    } finally {
      setDeletingId(null);
    }
  };

  const handleFlag = async (msg: PlayerMessage, category: FlagCategory) => {
    if (flaggingId) return;
    setFlaggingId(msg.id);
    setFlagError(null);
    setFlagNotice(null);
    setSendError(null);
    try {
      await messageAPI.flagMessage(msg.id, FLAG_REASON_BY_CATEGORY[category]);
      setFlagMenuId(null);
      setFlagNotice('FLAGGED FOR MODERATION');
    } catch (err: unknown) {
      setFlagError(formatCommsFlagError(err));
    } finally {
      setFlaggingId(null);
    }
  };

  const handleSend = async () => {
    if (!compose || !composeContent.trim() || isSending) return;

    setIsSending(true);
    setSendError(null);
    setSendNotice(null);

    try {
      await sendPlayerMessage(
        compose.recipientId,
        composeContent.trim(),
        composeSubject.trim() || null,
        compose.replyToId || null
      );
      setComposeContent('');
      setSendNotice('TRANSMISSION SENT');
    } catch (error: unknown) {
      setSendError(formatCommsSendError(error));
    } finally {
      setIsSending(false);
    }
  };

  return (
    <div className="mfd-page-ops">
      <MFDPageHeader title="COMMS / CREW" accent={ACCENT} status="shipped" showTitle={false} />
      <MFDPageBody scrollKey="comms-crew">
        <MFDField label="UPLINK" value={isConnected ? 'LINK OK' : 'LINK DOWN'} accent={isConnected} />
        <MFDField label="UNREAD" value={unreadMessageCount ?? '—'} />

        <div className="mfd-page-comms-mode-tabs" role="tablist" aria-label="Comms mode">
          <button
            type="button"
            role="tab"
            aria-selected={commsMode === 'hails'}
            className={`mfd-page-comms-mode-tab ${commsMode === 'hails' ? 'active' : ''}`}
            onClick={() => switchCommsMode('hails')}
          >
            HAILS
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={commsMode === 'threads'}
            className={`mfd-page-comms-mode-tab ${commsMode === 'threads' ? 'active' : ''}`}
            onClick={() => switchCommsMode('threads')}
          >
            THREADS
          </button>
        </div>

        <div className="mfd-page-section-label">
          {commsMode === 'hails' ? 'TRANSMISSIONS' : 'THREADS'}
        </div>
        {commsMode === 'hails' ? (
        <div className="mfd-page-comms-inbox">
          {inboxMessages.length > 0 ? (
            inboxMessages.map((msg) => (
              <div
                key={msg.id}
                className={`mfd-page-comms-hail-item ${msg.is_read ? 'read' : 'unread'}`}
              >
                <button
                  className="mfd-page-comms-hail-summary"
                  onClick={() => toggleExpand(msg)}
                  aria-expanded={expandedId === msg.id}
                >
                  <span
                    className={`mfd-page-comms-unread-dot ${msg.is_read ? 'off' : ''}`}
                    aria-hidden="true"
                  />
                  <span className="mfd-page-comms-hail-sender">
                    <PlayerNamePlate
                      name={(msg.sender_name || 'UNKNOWN').toUpperCase()}
                      size="sm"
                      pinnedMedalId={msg.sender_pinned_medal_id}
                      medalCount={msg.sender_medal_count}
                    />
                  </span>
                  <span className="mfd-page-comms-hail-subject">
                    {msg.subject || '(NO SUBJECT)'}
                  </span>
                  <span className="mfd-page-comms-hail-time">{timeAgo(msg.sent_at)}</span>
                </button>
                {expandedId === msg.id && (
                  <div className="mfd-page-comms-hail-body">
                    <p className="mfd-page-comms-hail-content">{msg.content}</p>
                    <div className="mfd-page-comms-hail-actions">
                      <button className="mfd-page-comms-reply-btn" onClick={() => startReply(msg)}>
                        ↩ REPLY
                      </button>
                      <button
                        className="mfd-page-comms-flag-btn"
                        data-testid="comms-flag-hail"
                        disabled={flaggingId === msg.id || !!msg.flagged}
                        onClick={() =>
                          setFlagMenuId((cur) => (cur === msg.id ? null : msg.id))
                        }
                        title="Flag this transmission for moderation"
                      >
                        {msg.flagged ? 'FLAGGED' : flagMenuId === msg.id ? 'CANCEL FLAG' : '⚑ FLAG'}
                      </button>
                      <button
                        className="mfd-page-comms-delete-btn"
                        data-testid="comms-purge-hail"
                        disabled={deletingId === msg.id}
                        onClick={() => handleDelete(msg)}
                        title="Purge this transmission from your inbox"
                      >
                        {deletingId === msg.id ? 'PURGING…' : '✕ PURGE'}
                      </button>
                    </div>
                    {flagMenuId === msg.id && !msg.flagged && (
                      <div
                        className="mfd-page-comms-flag-categories"
                        data-testid="comms-flag-categories"
                      >
                        {FLAG_CATEGORIES.map((cat) => (
                          <button
                            key={cat}
                            type="button"
                            className="mfd-page-comms-flag-cat-btn"
                            data-testid={`comms-flag-cat-${cat}`}
                            disabled={flaggingId === msg.id}
                            onClick={() => handleFlag(msg, cat)}
                          >
                            {flaggingId === msg.id ? 'FLAGGING…' : cat.replace('_', ' ').toUpperCase()}
                          </button>
                        ))}
                      </div>
                    )}
                    {flagNotice && expandedId === msg.id && (
                      <div className="mfd-page-comms-flag-notice" role="status">
                        {flagNotice}
                      </div>
                    )}
                    {flagError && expandedId === msg.id && (
                      <div className="mfd-page-comms-flag-error" role="alert">
                        {flagError}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))
          ) : (
            <MFDEmpty text="NO TRANSMISSIONS" />
          )}
        </div>
        ) : (
          <>
            {conversationsLoading && <MFDEmpty text="LOADING THREADS…" />}
            {!conversationsLoading && conversationsError && (
              <div className="mfd-page-warnline" role="alert">{conversationsError}</div>
            )}
            {!conversationsLoading && !conversationsError && (
              <div className="mfd-page-comms-inbox" data-testid="comms-threads-list">
                {conversations.length > 0 ? (
                  conversations.map((msg) => {
                    const tid = msg.thread_id || msg.id;
                    const isSelected = selectedThreadId === tid;
                    return (
                      <div
                        key={tid}
                        className={`mfd-page-comms-hail-item ${isSelected ? 'unread' : 'read'}`}
                      >
                        <button
                          type="button"
                          className="mfd-page-comms-hail-summary"
                          onClick={() => selectThread(msg)}
                          aria-expanded={isSelected}
                        >
                          <span className="mfd-page-comms-hail-sender">
                            {conversationPartyLabel(msg, playerState?.id)}
                          </span>
                          <span className="mfd-page-comms-hail-subject">
                            {msg.subject || msg.content?.slice(0, 40) || '(NO SUBJECT)'}
                          </span>
                          <span className="mfd-page-comms-hail-time">{timeAgo(msg.sent_at)}</span>
                        </button>
                      </div>
                    );
                  })
                ) : (
                  <MFDEmpty text="NO THREADS" />
                )}
              </div>
            )}
            {selectedThreadId && threadMessages.length > 0 && (
              <div className="mfd-page-comms-thread-detail" data-testid="comms-thread-detail">
                <div className="mfd-page-section-label">THREAD MESSAGES</div>
                {threadMessages.map((msg) => (
                  <div
                    key={msg.id}
                    className={`mfd-page-comms-hail-item ${msg.is_read ? 'read' : 'unread'}`}
                  >
                    <button
                      type="button"
                      className="mfd-page-comms-hail-summary"
                      onClick={() => {
                        if (expandedThreadMsgId === msg.id) {
                          setExpandedThreadMsgId(null);
                          setFlagMenuId(null);
                          return;
                        }
                        setExpandedThreadMsgId(msg.id);
                        setFlagMenuId(null);
                        if (!msg.is_read) {
                          markMessageRead(msg.id).catch(err =>
                            console.warn('CommsCrewPage: failed to mark thread message read:', err)
                          );
                        }
                      }}
                      aria-expanded={expandedThreadMsgId === msg.id}
                    >
                      <span className="mfd-page-comms-hail-sender">
                        <PlayerNamePlate
                          name={(msg.sender_name || 'UNKNOWN').toUpperCase()}
                          size="sm"
                          pinnedMedalId={msg.sender_pinned_medal_id}
                          medalCount={msg.sender_medal_count}
                        />
                      </span>
                      <span className="mfd-page-comms-hail-time">{timeAgo(msg.sent_at)}</span>
                    </button>
                    {expandedThreadMsgId === msg.id && (
                      <div className="mfd-page-comms-hail-body">
                        <p className="mfd-page-comms-hail-content">{msg.content}</p>
                        <div className="mfd-page-comms-hail-actions">
                          <button className="mfd-page-comms-reply-btn" onClick={() => startReply(msg)}>
                            ↩ REPLY
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {compose ? (
          <div className="mfd-page-comms-compose">
            <div className="mfd-page-comms-compose-to">
              <span className="mfd-page-comms-compose-to-label">TO:</span>
              <span className="mfd-page-comms-compose-recipient">
                {compose.recipientName.toUpperCase()}
              </span>
              {compose.replyToId && <span className="mfd-page-comms-compose-tag">REPLY</span>}
              <button
                className="mfd-page-comms-compose-clear"
                onClick={clearCompose}
                aria-label="Discard transmission"
              >
                ×
              </button>
            </div>
            <input
              className="mfd-page-comms-compose-subject"
              type="text"
              value={composeSubject}
              onChange={(e) => setComposeSubject(e.target.value)}
              placeholder="SUBJECT (OPTIONAL)"
              maxLength={255}
            />
            <textarea
              className="mfd-page-comms-compose-content"
              value={composeContent}
              onChange={(e) => {
                setComposeContent(e.target.value);
                if (sendNotice) setSendNotice(null);
              }}
              placeholder="TRANSMISSION TEXT…"
              maxLength={5000}
              rows={2}
            />
            <button
              className="mfd-page-comms-transmit-btn"
              onClick={handleSend}
              disabled={!composeContent.trim() || isSending}
            >
              {isSending ? 'TRANSMITTING…' : '▸ TRANSMIT'}
            </button>
            {sendError && <div className="mfd-page-warnline">{sendError}</div>}
            {sendNotice && <div className="mfd-page-comms-send-notice">{sendNotice}</div>}
          </div>
        ) : (
          <div className="mfd-page-comms-compose-hint">
            HAIL A CONTACT OR REPLY TO A TRANSMISSION TO OPEN A CHANNEL
          </div>
        )}
        {sendError && !compose && (
          <div className="mfd-page-warnline" role="alert">{sendError}</div>
        )}

        <div className="mfd-page-section-label">CONTACTS IN SECTOR</div>
        {contacts.length === 0 ? (
          <MFDEmpty text="NO CONTACTS IN SECTOR" />
        ) : (
          <ul className="mfd-page-comms-contacts">
            {contacts.map((c) => {
              const name = contactDisplayName(c);
              const key = (c.is_npc && c.player_id) || c.user_id || c.id || name;
              return (
                <li
                  key={key}
                  className="mfd-page-comms-contact"
                  style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.4rem' }}
                >
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name}</span>
                  {c.is_npc && <span className="mfd-page-npc-badge" style={{ flexShrink: 0, fontSize: '0.5rem', fontWeight: 700, letterSpacing: '0.06em', padding: '0.05rem 0.3rem', border: '1px solid rgba(0,217,255,0.45)', borderRadius: '3px', color: '#00d9ff' }}>NPC</span>}
                  {!c.is_npc && c.player_id && (
                    <button
                      className="mfd-page-comms-hail-btn"
                      onClick={() => startHail(c)}
                      title={`Open a hail to ${name}`}
                    >
                      HAIL
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        )}

        <div className="mfd-page-section-label">CREW</div>
        {playerState?.team_id ? (
          <MFDField label="AFFILIATION" value={teamLabel || 'ACTIVE'} accent />
        ) : (
          <MFDEmpty text="NO CREW AFFILIATION" />
        )}
      </MFDPageBody>
    </div>
  );
};

export default React.memo(CommsCrewPage);
