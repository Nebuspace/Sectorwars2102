import React, { useState, useEffect, useRef, useCallback } from 'react';
import { gameAPI } from '../../services/api';
import type { TeamMember, TeamMessageApiResponse } from '../../types/team';
import './team-chat.css';

interface TeamChatProps {
  teamId: string;
  playerId: string;
  /** Mapped roster from TeamManager — used for the member count and sender roles */
  members: TeamMember[];
}

export const TeamChat: React.FC<TeamChatProps> = ({ teamId, playerId, members }) => {
  const [messages, setMessages] = useState<TeamMessageApiResponse[]>([]);
  const [newMessage, setNewMessage] = useState('');
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Role lookup so we can badge a sender even though MessageResponse omits role.
  const roleById = new Map(members.map(m => [m.playerId, m.role]));

  const loadMessages = useCallback(async () => {
    try {
      // Backend GET /messages returns a bare List[MessageResponse] (newest first).
      const data = await gameAPI.team.getMessages(teamId, 100) as TeamMessageApiResponse[];
      const ordered = Array.isArray(data) ? [...data].reverse() : [];
      setMessages(ordered);
    } catch (error) {
      console.error('Failed to load messages:', error);
    } finally {
      setLoading(false);
    }
  }, [teamId]);

  useEffect(() => {
    void loadMessages();
    const interval = setInterval(() => void loadMessages(), 5000);
    return () => clearInterval(interval);
  }, [loadMessages]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newMessage.trim() || sending) return;

    setSending(true);
    setSendError(null);
    try {
      // send_message commits before returning, so a re-fetch is authoritative
      // and avoids racing the 5s poll (no optimistic-append clobber / dupes).
      await gameAPI.team.sendMessage(teamId, newMessage.trim());
      setNewMessage('');
      await loadMessages();
    } catch (error) {
      setSendError(error instanceof Error ? error.message : 'Failed to send message.');
    } finally {
      setSending(false);
    }
  };

  const formatTimestamp = (timestamp: string) => {
    const date = new Date(timestamp);
    const diff = Date.now() - date.getTime();
    if (diff < 60000) return 'just now';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
    if (diff < 604800000) return `${Math.floor(diff / 86400000)}d ago`;
    return date.toLocaleDateString();
  };

  const renderMessage = (message: TeamMessageApiResponse) => {
    const isOwnMessage = message.sender_id === playerId;
    const role = roleById.get(message.sender_id);
    return (
      <div key={message.id} className={`chat-message message ${isOwnMessage ? 'own' : ''}`}>
        <div className="message-header">
          <span className="message-sender">
            {message.sender_name}
            <span className={`role-indicator ${role ?? ''}`}>
              {role === 'leader' && ' 👑'}
              {role === 'officer' && ' ⭐'}
            </span>
          </span>
          <span className="message-time">{formatTimestamp(message.sent_at)}</span>
        </div>
        <div className="message-content">{message.content}</div>
      </div>
    );
  };

  if (loading) {
    return <div className="team-chat loading">Loading chat…</div>;
  }

  return (
    <div className="team-chat">
      <div className="chat-header">
        <h3>Team Chat</h3>
        <div className="chat-info">
          {/* Real member count; no presence telemetry exists, so no online count */}
          <span className="member-count">👥 {members.length} member{members.length !== 1 ? 's' : ''}</span>
        </div>
      </div>

      <div className="chat-messages">
        {messages.length === 0 ? (
          <div className="no-messages">
            <p>No messages yet. Start the conversation!</p>
          </div>
        ) : (
          messages.map(renderMessage)
        )}
        <div ref={messagesEndRef} />
      </div>

      <form className="chat-input-form" onSubmit={handleSendMessage}>
        <div className="input-wrapper">
          <input
            type="text"
            value={newMessage}
            onChange={(e) => setNewMessage(e.target.value)}
            placeholder="Type your message…"
            disabled={sending}
            maxLength={500}
          />
          <button type="submit" disabled={!newMessage.trim() || sending}>
            {sending ? '…' : 'Send'}
          </button>
        </div>
        <div className="input-info">
          <span className="char-count">{newMessage.length}/500</span>
          {sendError && <span className="form-error" role="alert">{sendError}</span>}
        </div>
      </form>
    </div>
  );
};
