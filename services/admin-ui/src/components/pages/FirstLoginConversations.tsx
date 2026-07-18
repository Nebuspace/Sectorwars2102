import React, { useState, useEffect, useCallback } from 'react';
import PageHeader from '../ui/PageHeader';
import { ConversationFilters } from '../first-login/ConversationFilters';
import { ConversationTable } from '../first-login/ConversationTable';
import { ConversationDetailModal } from '../first-login/ConversationDetailModal';
import {
  ConversationSummary,
  ConversationDetail,
  ConversationFilters as Filters
} from '../../types/firstLogin';
import { api } from '../../utils/auth';
import '../../styles/first-login-conversations.css';

const FirstLoginConversations: React.FC = () => {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [selectedConversation, setSelectedConversation] = useState<ConversationDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>({
    limit: 50,
    skip: 0
  });
  const [page, setPage] = useState(1);
  /** True when this page came back full — API returns a bare array, no total count. */
  const [hasMore, setHasMore] = useState(false);

  const fetchConversations = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const queryParams = new URLSearchParams();
      queryParams.append('skip', (filters.skip || 0).toString());
      queryParams.append('limit', (filters.limit || 50).toString());

      if (filters.outcome) queryParams.append('outcome', filters.outcome);
      if (filters.ai_provider) queryParams.append('ai_provider', filters.ai_provider);
      if (filters.start_date) queryParams.append('start_date', filters.start_date);
      if (filters.end_date) queryParams.append('end_date', filters.end_date);

      const response = await api.get(
        `/api/v1/admin/first-login/conversations?${queryParams}`
      );

      setConversations(response.data);

      // Backend returns a bare array — no total. Don't invent "Page X of Y".
      setHasMore(response.data.length === (filters.limit || 50));
    } catch (err) {
      console.error('Error fetching conversations:', err);
      setError(err instanceof Error ? err.message : 'An error occurred');
    } finally {
      setLoading(false);
    }
  }, [filters, page]);

  useEffect(() => {
    fetchConversations();
  }, [fetchConversations]);

  const handleSelectConversation = async (sessionId: string) => {
    try {
      const response = await api.get(
        `/api/v1/admin/first-login/conversations/${sessionId}`
      );

      setSelectedConversation(response.data);
    } catch (err) {
      console.error('Error fetching conversation details:', err);
      setError(err instanceof Error ? err.message : 'Failed to load conversation details');
    }
  };

  const handleFilterChange = (newFilters: Filters) => {
    setFilters(newFilters);
    setPage(1); // Reset to first page when filters change
  };

  const handlePreviousPage = () => {
    if (page > 1) {
      setPage(p => p - 1);
      setFilters(f => ({ ...f, skip: ((page - 2) * (f.limit || 50)) }));
    }
  };

  const handleNextPage = () => {
    if (!hasMore) return;
    setPage(p => p + 1);
    setFilters(f => ({ ...f, skip: (page * (f.limit || 50)) }));
  };

  const handleExportConversation = (conversation: ConversationDetail) => {
    const dataStr = JSON.stringify(conversation, null, 2);
    const blob = new Blob([dataStr], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `conversation-${conversation.session.session_id}-${new Date().toISOString().split('T')[0]}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handleExportAll = () => {
    const csv = [
      'Player,Started,Completed,Ship Claimed,Ship Awarded,Outcome,Questions,Cost,AI Providers',
      ...conversations.map(c =>
        `"${c.player_username}","${c.started_at}","${c.completed_at || ''}","${c.ship_claimed || ''}","${c.awarded_ship || ''}","${c.outcome || ''}",${c.total_questions},$${c.total_cost_usd.toFixed(4)},"${c.ai_providers_used.join(', ')}"`
      )
    ].join('\n');

    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `first-login-conversations-${new Date().toISOString().split('T')[0]}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="first-login-conversations-page">
      <PageHeader
        title="First Login Conversations"
        subtitle="View and analyze player registration dialogues with AI guards"
      />

      <div className="page-content">
        {/* Filters */}
        <ConversationFilters
          filters={filters}
          onFilterChange={handleFilterChange}
          onRefresh={fetchConversations}
          loading={loading}
        />

        {/* Export button */}
        <div className="toolbar">
          <button
            className="btn btn-secondary"
            onClick={handleExportAll}
            disabled={conversations.length === 0}
          >
            <i className="fas fa-download"></i>
            Export All (CSV)
          </button>
          <span className="result-count">
            {conversations.length} conversation{conversations.length !== 1 ? 's' : ''} found
          </span>
        </div>

        {/* Error message */}
        {error && (
          <div className="error-banner">
            <i className="fas fa-exclamation-circle"></i>
            {error}
          </div>
        )}

        {/* Conversations table */}
        <ConversationTable
          conversations={conversations}
          onSelectConversation={handleSelectConversation}
          loading={loading}
        />

        {/* Pagination */}
        {!loading && conversations.length > 0 && (
          <div className="pagination">
            <button
              className="btn btn-secondary"
              onClick={handlePreviousPage}
              disabled={page === 1}
            >
              <i className="fas fa-chevron-left"></i>
              Previous
            </button>
            <span className="page-info" title="API returns no total count — page numbers are sequential only">
              Page {page}{hasMore ? '+' : ''}
              <span className="page-info-hint"> · no total from API</span>
            </span>
            <button
              className="btn btn-secondary"
              onClick={handleNextPage}
              disabled={!hasMore}
            >
              Next
              <i className="fas fa-chevron-right"></i>
            </button>
          </div>
        )}
      </div>

      {/* Detail modal */}
      <ConversationDetailModal
        conversation={selectedConversation}
        onClose={() => setSelectedConversation(null)}
        onExport={handleExportConversation}
      />
    </div>
  );
};

export default FirstLoginConversations;
