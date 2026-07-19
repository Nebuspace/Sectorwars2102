import React, { useState, useEffect, useCallback } from 'react';
import './audit-log-viewer.css';

interface AuditLog {
  id: string;
  timestamp: string;
  userId: string;
  username: string;
  action: string;
  resource: string;
  resourceId?: string;
  details: Record<string, any>;
  ipAddress: string;
  userAgent: string;
  status: 'success' | 'failure' | 'warning';
  duration?: number;
}

interface AuditLogViewerProps {
  filters?: {
    userId?: string;
    action?: string;
    resource?: string;
    status?: string;
    dateFrom?: string;
    dateTo?: string;
  };
  onExport?: (logs: AuditLog[]) => void;
}

export const AuditLogViewer: React.FC<AuditLogViewerProps> = ({ filters = {}, onExport }) => {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [selectedLog, setSelectedLog] = useState<AuditLog | null>(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [sortField, setSortField] = useState<keyof AuditLog>('timestamp');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');

  const fetchAuditLogs = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const queryParams = new URLSearchParams({
        page: page.toString(),
        limit: '50',
        ...filters,
        search: searchTerm,
        sortField,
        sortOrder
      });

      const response = await fetch(`/api/v1/admin/audit/logs?${queryParams}`, {
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('accessToken')}`
        }
      });

      if (!response.ok) {
        throw new Error('Failed to load audit logs');
      }

      const data = await response.json();
      setLogs(data.logs || []);
      setTotalPages(data.totalPages || 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred');
    } finally {
      setLoading(false);
    }
  }, [page, filters, searchTerm, sortField, sortOrder]);

  useEffect(() => {
    fetchAuditLogs();
  }, [fetchAuditLogs]);

  const handleSort = (field: keyof AuditLog) => {
    if (sortField === field) {
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortOrder('desc');
    }
  };

  const getActionIcon = (action: string) => {
    const icons: Record<string, string> = {
      login: 'fa-sign-in-alt',
      logout: 'fa-sign-out-alt',
      update_ship: 'fa-rocket',
      delete_player: 'fa-user-times',
      market_intervention: 'fa-chart-line',
      ban_player: 'fa-ban',
      create: 'fa-plus',
      update: 'fa-edit',
      delete: 'fa-trash'
    };
    return icons[action] || 'fa-cog';
  };

  const getStatusClass = (status: string) => {
    return `status-${status}`;
  };

  const formatDuration = (ms?: number) => {
    if (!ms) return '-';
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  };

  const handleExport = () => {
    if (onExport) {
      onExport(logs);
    } else {
      // Default CSV export
      const csv = [
        'Timestamp,User,Action,Resource,Status,IP Address,Duration',
        ...logs.map(log => 
          `"${log.timestamp}","${log.username}","${log.action}","${log.resource}","${log.status}","${log.ipAddress}","${formatDuration(log.duration)}"`
        )
      ].join('\n');

      const blob = new Blob([csv], { type: 'text/csv' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `audit-logs-${new Date().toISOString().split('T')[0]}.csv`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }
  };

  return (
    <div className="audit-log-viewer">
      <div className="audit-header">
        <h2>Legacy HTTP Audit Trail</h2>
        <div className="audit-actions">
          <div className="search-box">
            <i className="fas fa-search"></i>
            <input
              type="text"
              placeholder="Search logs..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
          </div>
          <button className="btn btn-secondary" onClick={handleExport}>
            <i className="fas fa-download"></i>
            Export
          </button>
          <button className="btn btn-primary" onClick={fetchAuditLogs}>
            <i className="fas fa-sync"></i>
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="audit-error">
          <i className="fas fa-exclamation-circle"></i>
          {error}
        </div>
      )}

      {loading ? (
        <div className="audit-loading">
          <i className="fas fa-spinner fa-spin"></i>
          <span>Loading audit logs...</span>
        </div>
      ) : (
        <>
          <div className="audit-table-container">
            <table className="audit-table">
              <thead>
                <tr>
                  <th onClick={() => handleSort('timestamp')}>
                    Timestamp
                    {sortField === 'timestamp' && (
                      <i className={`fas fa-chevron-${sortOrder === 'asc' ? 'up' : 'down'}`}></i>
                    )}
                  </th>
                  <th onClick={() => handleSort('username')}>
                    User
                    {sortField === 'username' && (
                      <i className={`fas fa-chevron-${sortOrder === 'asc' ? 'up' : 'down'}`}></i>
                    )}
                  </th>
                  <th onClick={() => handleSort('action')}>
                    Action
                    {sortField === 'action' && (
                      <i className={`fas fa-chevron-${sortOrder === 'asc' ? 'up' : 'down'}`}></i>
                    )}
                  </th>
                  <th onClick={() => handleSort('resource')}>
                    Resource
                    {sortField === 'resource' && (
                      <i className={`fas fa-chevron-${sortOrder === 'asc' ? 'up' : 'down'}`}></i>
                    )}
                  </th>
                  <th>Status</th>
                  <th>IP Address</th>
                  <th>Duration</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {logs.length === 0 && (
                  <tr>
                    <td colSpan={8} style={{ textAlign: 'center', padding: '40px 20px', color: '#9ca3af' }}>
                      <i className="fas fa-clipboard-list" style={{ fontSize: '2rem', marginBottom: '12px', display: 'block' }}></i>
                      No audit logs found.
                    </td>
                  </tr>
                )}
                {logs.map(log => (
                  <tr key={log.id} className={getStatusClass(log.status)}>
                    <td className="timestamp">
                      {new Date(log.timestamp).toLocaleString()}
                    </td>
                    <td className="user">
                      <span className="username">{log.username}</span>
                    </td>
                    <td className="action">
                      <i className={`fas ${getActionIcon(log.action)}`}></i>
                      <span>{log.action.replace(/_/g, ' ')}</span>
                    </td>
                    <td className="resource">
                      {log.resource}
                      {log.resourceId && (
                        <span className="resource-id">#{log.resourceId}</span>
                      )}
                    </td>
                    <td className="status">
                      <span className={`status-badge ${log.status}`}>
                        {log.status}
                      </span>
                    </td>
                    <td className="ip-address">{log.ipAddress}</td>
                    <td className="duration">{formatDuration(log.duration)}</td>
                    <td className="actions">
                      <button
                        className="btn-icon"
                        onClick={() => setSelectedLog(log)}
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

          <div className="audit-pagination">
            <button
              className="btn btn-secondary"
              onClick={() => setPage(p => Math.max(1, p - 1))}
              disabled={page === 1}
            >
              <i className="fas fa-chevron-left"></i>
              Previous
            </button>
            <span className="page-info">
              Page {page} of {totalPages}
            </span>
            <button
              className="btn btn-secondary"
              onClick={() => setPage(p => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
            >
              Next
              <i className="fas fa-chevron-right"></i>
            </button>
          </div>
        </>
      )}

      {selectedLog && (
        <div className="audit-modal" onClick={() => setSelectedLog(null)}>
          <div className="audit-modal-content" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Audit Log Details</h3>
              <button className="close-button" onClick={() => setSelectedLog(null)}>
                <i className="fas fa-times"></i>
              </button>
            </div>
            <div className="modal-body">
              <div className="detail-row">
                <span className="label">ID:</span>
                <span className="value">{selectedLog.id}</span>
              </div>
              <div className="detail-row">
                <span className="label">Timestamp:</span>
                <span className="value">{new Date(selectedLog.timestamp).toLocaleString()}</span>
              </div>
              <div className="detail-row">
                <span className="label">User:</span>
                <span className="value">{selectedLog.username} (ID: {selectedLog.userId})</span>
              </div>
              <div className="detail-row">
                <span className="label">Action:</span>
                <span className="value">{selectedLog.action}</span>
              </div>
              <div className="detail-row">
                <span className="label">Resource:</span>
                <span className="value">
                  {selectedLog.resource}
                  {selectedLog.resourceId && ` #${selectedLog.resourceId}`}
                </span>
              </div>
              <div className="detail-row">
                <span className="label">Status:</span>
                <span className={`status-badge ${selectedLog.status}`}>
                  {selectedLog.status}
                </span>
              </div>
              <div className="detail-row">
                <span className="label">IP Address:</span>
                <span className="value">{selectedLog.ipAddress}</span>
              </div>
              <div className="detail-row">
                <span className="label">User Agent:</span>
                <span className="value user-agent">{selectedLog.userAgent}</span>
              </div>
              {selectedLog.duration && (
                <div className="detail-row">
                  <span className="label">Duration:</span>
                  <span className="value">{formatDuration(selectedLog.duration)}</span>
                </div>
              )}
              {Object.keys(selectedLog.details).length > 0 && (
                <div className="detail-section">
                  <h4>Additional Details</h4>
                  <pre className="details-json">
                    {JSON.stringify(selectedLog.details, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};