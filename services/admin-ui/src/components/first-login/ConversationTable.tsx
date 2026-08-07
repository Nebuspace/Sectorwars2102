import React from 'react';
import { ConversationSummary } from '../../types/firstLogin';

interface ConversationTableProps {
  conversations: ConversationSummary[];
  onSelectConversation: (sessionId: string) => void;
  loading?: boolean;
}

export const ConversationTable: React.FC<ConversationTableProps> = ({
  conversations,
  onSelectConversation,
  loading = false
}) => {
  const getOutcomeBadgeClass = (outcome: string | null): string => {
    if (!outcome) return 'outcome-unknown';
    switch (outcome.toUpperCase()) {
      case 'SUCCESS':
        return 'outcome-success';
      case 'CAUGHT':
        return 'outcome-caught';
      case 'SUSPICIOUS':
        return 'outcome-suspicious';
      case 'ABANDONED':
        return 'outcome-abandoned';
      default:
        return 'outcome-unknown';
    }
  };

  const getOutcomeIcon = (outcome: string | null): string => {
    if (!outcome) return '❓';
    switch (outcome.toUpperCase()) {
      case 'SUCCESS':
        return '✅';
      case 'CAUGHT':
        return '🚫';
      case 'SUSPICIOUS':
        return '⚠️';
      case 'ABANDONED':
        return '🏃';
      default:
        return '❓';
    }
  };

  const formatDate = (dateStr: string | null): string => {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    return date.toLocaleString();
  };

  const formatCost = (cost: number): string => {
    return `$${cost.toFixed(4)}`;
  };

  const formatShipType = (shipType: string | null): string => {
    if (!shipType) return '-';
    return shipType
      .replace(/_/g, ' ')
      .toLowerCase()
      .replace(/\b\w/g, c => c.toUpperCase());
  };

  if (loading && conversations.length === 0) {
    return (
      <div className="table-loading">
        <i className="fas fa-spinner fa-spin"></i>
        <span>Loading conversations...</span>
      </div>
    );
  }

  if (conversations.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-icon">💬</div>
        <h3>No First Login Conversations Yet</h3>
        <p>Conversations will appear here when players complete their first registration dialogue.</p>
      </div>
    );
  }

  return (
    <div className="conversation-table-container">
      <table className="conversation-table">
        <thead>
          <tr>
            <th>Player</th>
            <th>Started</th>
            <th>Completed</th>
            <th>Ship Claimed</th>
            <th>Awarded</th>
            <th>Outcome</th>
            <th>Questions</th>
            <th>AI Providers</th>
            <th>Cost</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {conversations.map((conversation) => (
            <tr key={conversation.session_id}>
              <td className="player-cell">
                <span className="player-username">{conversation.player_username}</span>
              </td>
              <td className="date-cell">
                {formatDate(conversation.started_at)}
              </td>
              <td className="date-cell">
                {conversation.completed_at ? (
                  formatDate(conversation.completed_at)
                ) : (
                  <span className="in-progress-badge">⏳ In Progress</span>
                )}
              </td>
              <td className="ship-cell">
                {formatShipType(conversation.ship_claimed)}
              </td>
              <td className="ship-cell">
                {formatShipType(conversation.awarded_ship)}
              </td>
              <td className="outcome-cell">
                {conversation.outcome ? (
                  <span className={`outcome-badge ${getOutcomeBadgeClass(conversation.outcome)}`}>
                    {getOutcomeIcon(conversation.outcome)} {conversation.outcome}
                  </span>
                ) : (
                  <span className="outcome-badge outcome-unknown">❓ Unknown</span>
                )}
              </td>
              <td className="numeric-cell">
                {conversation.total_questions}
              </td>
              <td className="providers-cell">
                <div className="provider-tags">
                  {conversation.ai_providers_used.map((provider, idx) => (
                    <span key={idx} className={`provider-tag provider-${provider}`}>
                      {provider}
                    </span>
                  ))}
                  {conversation.ai_providers_used.length === 0 && (
                    <span className="provider-tag provider-none">none</span>
                  )}
                </div>
              </td>
              <td className="cost-cell">
                <span className={conversation.total_cost_usd > 0.10 ? 'cost-high' : 'cost-normal'}>
                  {formatCost(conversation.total_cost_usd)}
                  {conversation.total_cost_usd > 0.10 && ' ⚠️'}
                </span>
              </td>
              <td className="actions-cell">
                <button
                  className="btn-icon"
                  onClick={() => onSelectConversation(conversation.session_id)}
                  title="View details"
                >
                  <i className="fas fa-eye"></i>
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
