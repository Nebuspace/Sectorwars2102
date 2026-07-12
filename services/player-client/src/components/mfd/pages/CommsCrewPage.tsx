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
 * (inboxMessages/refreshInbox/sendPlayerMessage/markMessageRead), same two
 * recipient sources: HAIL a sector contact (player_id required — NPCs and
 * live-WS-only contacts without a snapshot entry don't get a HAIL button)
 * or REPLY to an inbox message. No manual recipient entry in v1.
 */

import React from 'react';
import { useGame, type PlayerMessage } from '../../../contexts/GameContext';
import { useWebSocket } from '../../../contexts/WebSocketContext';
import { useAuth } from '../../../contexts/AuthContext';
import { teamAPI } from '../../../services/api';
import { MFDPageHeader, MFDPageBody, MFDField, MFDEmpty } from '../atoms';
import './pages-ops.css';

const ACCENT = '#00FF7F';

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

const CommsCrewPage: React.FC = () => {
  const {
    playerState,
    currentSector,
    unreadMessageCount,
    inboxMessages,
    refreshInbox,
    sendPlayerMessage,
    markMessageRead,
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
    <div className="mfd-page-ops">
      <MFDPageHeader title="COMMS / CREW" accent={ACCENT} status="shipped" />
      <MFDPageBody scrollKey="comms-crew">
        <MFDField label="UPLINK" value={isConnected ? 'LINK OK' : 'LINK DOWN'} accent={isConnected} />
        <MFDField label="UNREAD" value={unreadMessageCount ?? '—'} />

        <div className="mfd-page-section-label">TRANSMISSIONS</div>
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
                    {(msg.sender_name || 'UNKNOWN').toUpperCase()}
                  </span>
                  <span className="mfd-page-comms-hail-subject">
                    {msg.subject || '(NO SUBJECT)'}
                  </span>
                  <span className="mfd-page-comms-hail-time">{timeAgo(msg.sent_at)}</span>
                </button>
                {expandedId === msg.id && (
                  <div className="mfd-page-comms-hail-body">
                    <p className="mfd-page-comms-hail-content">{msg.content}</p>
                    <button className="mfd-page-comms-reply-btn" onClick={() => startReply(msg)}>
                      ↩ REPLY
                    </button>
                  </div>
                )}
              </div>
            ))
          ) : (
            <MFDEmpty text="NO TRANSMISSIONS" />
          )}
        </div>

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
