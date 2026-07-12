import React, { useEffect, useRef, useState } from 'react';
import { useGame, type PlayerMessage } from '../../contexts/GameContext';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { useAuth } from '../../contexts/AuthContext';
import DeckPageTabs from '../cockpit/DeckPageTabs';
import './comms-mailbox.css';

/**
 * CommsMailbox — the COMMS monitor's full display: a two-mode console
 * switching between CONTACTS (live sector presence, unchanged behavior)
 * and HAILS (the player-to-player mailbox bound to /api/v1/messages/*).
 *
 * Renders the monitor's screen-hud-header (with mode switch + unread
 * badge) and screen-hud-content, so GameDashboard's COMMS block only
 * provides the bezel/screen chrome around it.
 *
 * Recipient sourcing (v1): a hail needs a recipient Player id. The two
 * sources are (1) the HAIL button on a CONTACTS row — sector-presence
 * snapshot entries carry player_id for real players — and (2) REPLY on
 * an inbox message (sender_id is a Player id). There is intentionally no
 * manual recipient entry in v1. Live-WS-only contacts (user_id only, no
 * snapshot entry yet) and NPCs don't get a HAIL button: the former can't
 * be resolved to a Player id client-side, the latter aren't messageable
 * (recipient must exist in the players table).
 */

interface SectorContact {
  player_id?: string;
  user_id?: string;
  id?: string;
  ship_id?: string;
  username?: string;
  name?: string;
  is_npc?: boolean;
  name_color?: string;
  military_rank?: string;
  reputation_tier?: string;
  personal_reputation?: number;
}

interface CommsMailboxProps {
  contacts: SectorContact[];
  /** ship_id of the currently selected contact (spotlit in the viewport). */
  selectedShipId?: string | null;
  /** Clicking a contact row selects its ship in the cockpit viewport. */
  onSelectContact?: (contact: SectorContact | null) => void;
}

interface ComposeTarget {
  recipientId: string;
  recipientName: string;
  replyToId?: string;
}

// Compact relative timestamp for the hail list (CRT-terse)
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

const contactDisplayName = (contact: SectorContact): string =>
  contact.username || contact.name || 'UNKNOWN CONTACT';

const CommsMailbox: React.FC<CommsMailboxProps> = ({ contacts, selectedShipId, onSelectContact }) => {
  const {
    inboxMessages,
    unreadMessageCount,
    refreshInbox,
    sendPlayerMessage,
    markMessageRead
  } = useGame();
  const { newMessageSignal } = useWebSocket();
  const { user } = useAuth();

  const [mode, setMode] = useState<'contacts' | 'hails'>('contacts');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [compose, setCompose] = useState<ComposeTarget | null>(null);
  const [composeSubject, setComposeSubject] = useState('');
  const [composeContent, setComposeContent] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [sendNotice, setSendNotice] = useState<string | null>(null);

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
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
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

  const toggleExpand = (msg: PlayerMessage) => {
    if (expandedId === msg.id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(msg.id);
    if (!msg.is_read) {
      // Reading IS the read receipt — but a failed flag write must not
      // block reading the transmission itself.
      markMessageRead(msg.id).catch(err =>
        console.warn('CommsMailbox: failed to mark message read:', err)
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

  const startHail = (contact: SectorContact) => {
    if (!contact.player_id) return;
    setMode('hails');
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
    } catch (error: any) {
      // FastAPI 422s return `detail` as an array of validation objects, and a
      // raw object would render as "[object Object]" / crash the CRT line.
      // Only a plain string is safe to surface; anything else → generic.
      const rawDetail = error?.response?.data?.detail;
      const message =
        (typeof rawDetail === 'string' && rawDetail) ||
        (typeof error?.message === 'string' && error.message) ||
        'TRANSMISSION FAILED';
      setSendError(message);
    } finally {
      setIsSending(false);
    }
  };

  return (
    <>
      <div className="screen-hud-header comms-header-with-modes">
        <span className="comms-header-label">
          COMMS
          {unreadMessageCount > 0 && (
            <span className="comms-unread-badge">({unreadMessageCount})</span>
          )}
        </span>
        <DeckPageTabs
          pages={[
            { id: 'contacts', label: 'CONTACTS' },
            {
              id: 'hails',
              label: (
                <>
                  HAILS
                  {unreadMessageCount > 0 && <span className="comms-mode-dot" aria-hidden="true" />}
                </>
              ),
            },
          ]}
          activeId={mode}
          onSelect={(id) => setMode(id as 'contacts' | 'hails')}
          ariaLabel="COMMS display mode"
          accent="#00ff41"
          idBase="comms"
        />
      </div>
      <div
        className={`screen-hud-content ${mode === 'hails' ? 'comms-hails-content' : ''}`}
        role="tabpanel"
        id={`comms-panel-${mode}`}
        aria-labelledby={`comms-tab-${mode}`}
      >
        {mode === 'contacts' ? (
          contacts.length > 0 ? (
            <div className="contacts-compact-list">
              {contacts.map((player) => {
                const selectable = !!onSelectContact && !!player.ship_id;
                const selected = !!player.ship_id &&
                  String(player.ship_id) === String(selectedShipId ?? '');
                return (
                <div
                  key={(player.is_npc && player.player_id) || player.user_id || player.id || player.username}
                  className={`contact-list-item${selected ? ' selected' : ''}`}
                  onClick={selectable
                    ? () => onSelectContact!(selected ? null : player)
                    : undefined}
                  style={selectable ? { cursor: 'pointer' } : undefined}
                  title={selectable
                    ? (selected ? 'Deselect — clear viewport spotlight'
                                : `Spotlight ${contactDisplayName(player)} in the viewport`)
                    : undefined}
                >
                  <span className="status-indicator online"></span>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', minWidth: 0 }}>
                    <span
                      className="comms-contact-name"
                      style={{ color: player.name_color || '#FFFFFF' }}
                    >
                      <span className="comms-contact-name-text">
                        {player.military_rank ? `${player.military_rank.toUpperCase()} ` : ''}
                        {contactDisplayName(player)}
                      </span>
                      {player.is_npc && (
                        <span className="contact-npc-badge">NPC</span>
                      )}
                    </span>
                    {(player.reputation_tier || typeof player.personal_reputation === 'number') && (
                      <span style={{ fontSize: '0.7em', opacity: 0.7 }}>
                        {player.reputation_tier || 'Neutral'} ({(player.personal_reputation ?? 0) >= 0 ? '+' : ''}{player.personal_reputation ?? 0})
                      </span>
                    )}
                  </div>
                  {!player.is_npc && player.player_id && (
                    <button
                      className="comms-hail-btn"
                      onClick={(e) => { e.stopPropagation(); startHail(player); }}
                      title={`Open a hail to ${contactDisplayName(player)}`}
                    >
                      HAIL
                    </button>
                  )}
                </div>
                );
              })}
            </div>
          ) : (
            <div className="empty-state">No other contacts in sector</div>
          )
        ) : (
          <div className="comms-hails">
            <div className="comms-inbox-list">
              {inboxMessages.length > 0 ? (
                inboxMessages.map((msg) => (
                  <div
                    key={msg.id}
                    className={`comms-hail-item ${msg.is_read ? 'read' : 'unread'}`}
                  >
                    <button
                      className="comms-hail-summary"
                      onClick={() => toggleExpand(msg)}
                      aria-expanded={expandedId === msg.id}
                    >
                      <span
                        className={`comms-unread-dot ${msg.is_read ? 'off' : ''}`}
                        aria-hidden="true"
                      />
                      <span className="comms-hail-sender">
                        {(msg.sender_name || 'UNKNOWN').toUpperCase()}
                      </span>
                      <span className="comms-hail-subject">
                        {msg.subject || '(NO SUBJECT)'}
                      </span>
                      <span className="comms-hail-time">{timeAgo(msg.sent_at)}</span>
                    </button>
                    {expandedId === msg.id && (
                      <div className="comms-hail-body">
                        <p className="comms-hail-content">{msg.content}</p>
                        <button className="comms-reply-btn" onClick={() => startReply(msg)}>
                          ↩ REPLY
                        </button>
                      </div>
                    )}
                  </div>
                ))
              ) : (
                <div className="empty-state">NO TRANSMISSIONS</div>
              )}
            </div>
            {compose ? (
              <div className="comms-compose">
                <div className="comms-compose-to">
                  <span className="comms-compose-to-label">TO:</span>
                  <span className="comms-compose-recipient">
                    {compose.recipientName.toUpperCase()}
                  </span>
                  {compose.replyToId && <span className="comms-compose-tag">REPLY</span>}
                  <button
                    className="comms-compose-clear"
                    onClick={clearCompose}
                    aria-label="Discard transmission"
                  >
                    ×
                  </button>
                </div>
                <input
                  className="comms-compose-subject"
                  type="text"
                  value={composeSubject}
                  onChange={(e) => setComposeSubject(e.target.value)}
                  placeholder="SUBJECT (OPTIONAL)"
                  maxLength={255}
                />
                <textarea
                  className="comms-compose-content"
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
                  className="comms-transmit-btn"
                  onClick={handleSend}
                  disabled={!composeContent.trim() || isSending}
                >
                  {isSending ? 'TRANSMITTING…' : '▸ TRANSMIT'}
                </button>
                {sendError && <div className="comms-inline-error">{sendError}</div>}
                {sendNotice && <div className="comms-send-notice">{sendNotice}</div>}
              </div>
            ) : (
              <div className="comms-compose-hint">
                HAIL A CONTACT OR REPLY TO A TRANSMISSION TO OPEN A CHANNEL
              </div>
            )}
          </div>
        )}
      </div>
    </>
  );
};

export default CommsMailbox;
