import React, { useState } from 'react';
import PageHeader from '../ui/PageHeader';
import { RoleManagement } from '../permissions/RoleManagement';
import { PermissionMatrix } from '../permissions/PermissionMatrix';
import './permissions-dashboard.css';

export const PermissionsDashboard: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'roles' | 'matrix' | 'users'>('roles');
  const [selectedUser, setSelectedUser] = useState<any>(null);
  const [users, setUsers] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');

  React.useEffect(() => {
    if (activeTab === 'users') {
      fetchUsers();
    }
  }, [activeTab]);

  const fetchUsers = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('/api/v1/admin/users', {
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('accessToken')}`
        }
      });

      if (response.ok) {
        const data = await response.json();
        setUsers(data.users || []);
      } else {
        setUsers([]);
        setError('Failed to load users. Server returned an error.');
      }
    } catch (error) {
      console.error('Error fetching users:', error);
      setUsers([]);
      setError('Failed to load users. Please check your connection and try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleRoleUpdate = (role: any) => {
    console.log('Role updated:', role);
    // Refresh users if needed
    if (activeTab === 'users') {
      fetchUsers();
    }
  };

  const filteredUsers = users.filter(user =>
    (user.username || '').toLowerCase().includes(searchTerm.toLowerCase()) ||
    (user.email || '').toLowerCase().includes(searchTerm.toLowerCase())
  );

  return (
    <div className="permissions-dashboard">
      <PageHeader
        title="Permissions & Access Control"
        subtitle="View user access; role catalog APIs are not implemented"
      />

      <div className="permissions-tabs">
        <button
          className={`tab ${activeTab === 'roles' ? 'active' : ''}`}
          onClick={() => setActiveTab('roles')}
        >
          <i className="fas fa-user-tag"></i>
          Roles — unavailable
        </button>
        <button
          className={`tab ${activeTab === 'matrix' ? 'active' : ''}`}
          onClick={() => setActiveTab('matrix')}
        >
          <i className="fas fa-th"></i>
          Matrix — design-only
        </button>
        <button
          className={`tab ${activeTab === 'users' ? 'active' : ''}`}
          onClick={() => setActiveTab('users')}
        >
          <i className="fas fa-users"></i>
          User Permissions
        </button>
      </div>

      <div className="permissions-content">
        {activeTab === 'roles' && (
          <RoleManagement onRoleUpdate={handleRoleUpdate} />
        )}

        {activeTab === 'matrix' && (
          /* PermissionMatrix renders its own design-only banner (ADR-0027 /
             ADR-0058); the inert wrapper was only needed while it shipped a
             fabricated interactive matrix. */
          <PermissionMatrix />
        )}

        {activeTab === 'users' && (
          <div className="user-permissions">
            <div className="user-permissions-header">
              <h2>User Permission Management</h2>
              <div className="search-box">
                <i className="fas fa-search"></i>
                <input
                  type="text"
                  placeholder="Search users..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                />
              </div>
            </div>

            {error && (
              <div className="error-state" style={{ padding: '16px', margin: '16px 0', backgroundColor: 'rgba(231, 76, 60, 0.1)', border: '1px solid rgba(231, 76, 60, 0.3)', borderRadius: '8px', color: '#e74c3c' }}>
                <i className="fas fa-exclamation-circle" style={{ marginRight: '8px' }}></i>
                {error}
              </div>
            )}

            {loading ? (
              <div className="loading-state">
                <i className="fas fa-spinner fa-spin"></i>
                <span>Loading users...</span>
              </div>
            ) : (
              <div className="user-permissions-content">
                <div className="user-list">
                  {filteredUsers.map(user => (
                    <div
                      key={user.id}
                      className={`user-item ${selectedUser?.id === user.id ? 'selected' : ''} ${user.is_active === false ? 'inactive' : ''}`}
                      onClick={() => setSelectedUser(user)}
                    >
                      <div className="user-info">
                        <div className="user-header">
                          <span className="username">{user.username}</span>
                          <span className={`status ${user.is_active === false ? 'inactive' : 'active'}`}>
                            {user.is_active === false ? 'inactive' : 'active'}
                          </span>
                        </div>
                        <div className="user-email">{user.email}</div>
                        <div className="user-meta">
                          <span className="roles">
                            <i className="fas fa-user-tag"></i>
                            {user.roles?.length ? user.roles.join(', ') : '—'}
                          </span>
                          {(user.customPermissions?.length ?? 0) > 0 && (
                            <span className="custom-perms">
                              <i className="fas fa-key"></i>
                              +{user.customPermissions.length} custom
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>

                {selectedUser && (
                  <div className="user-detail">
                    <div className="user-detail-header">
                      <h3>{selectedUser.username}</h3>
                      <button
                        className="close-button"
                        onClick={() => setSelectedUser(null)}
                      >
                        <i className="fas fa-times"></i>
                      </button>
                    </div>

                    <div className="user-detail-content">
                      <div className="detail-section">
                        <h4>User Information</h4>
                        <div className="detail-row">
                          <span className="label">Email:</span>
                          <span className="value">{selectedUser.email}</span>
                        </div>
                        <div className="detail-row">
                          <span className="label">Status:</span>
                          <span className={`status ${selectedUser.is_active === false ? 'inactive' : 'active'}`}>
                            {selectedUser.is_active === false ? 'inactive' : 'active'}
                          </span>
                        </div>
                        <div className="detail-row">
                          <span className="label">Last Login:</span>
                          <span className="value">
                            {selectedUser.last_login ? new Date(selectedUser.last_login).toLocaleString() : '—'}
                          </span>
                        </div>
                      </div>

                      <div className="detail-section">
                        <h4>Assigned Roles</h4>
                        {selectedUser.roles?.length ? (
                          <div className="role-list">
                            {selectedUser.roles.map((roleId: string) => (
                              <div key={roleId} className="role-badge">
                                <i className="fas fa-user-tag"></i>
                                {roleId}
                              </div>
                            ))}
                          </div>
                        ) : (
                          <p className="no-permissions">—</p>
                        )}
                      </div>

                      <div className="detail-section">
                        <h4>Custom Permissions</h4>
                        {selectedUser.customPermissions?.length ? (
                          <div className="permission-list">
                            {selectedUser.customPermissions.map((permId: string) => (
                              <div key={permId} className="permission-badge">
                                <i className="fas fa-key"></i>
                                {permId}
                              </div>
                            ))}
                          </div>
                        ) : (
                          <p className="no-permissions">—</p>
                        )}
                      </div>

                      <div
                        role="note"
                        className="user-actions"
                        style={{
                          margin: '12px 0 0', padding: '10px 12px',
                          background: 'rgba(234, 179, 8, 0.12)', border: '1px solid rgba(234, 179, 8, 0.35)',
                          borderRadius: '6px', color: '#fbbf24', fontSize: '0.82rem', lineHeight: 1.4
                        }}
                      >
                        Permission edit / revoke is unavailable: the backend endpoint{' '}
                        <code style={{ color: '#fde68a' }}>PUT/DELETE /api/v1/admin/users/:id/permissions</code>{' '}
                        is not implemented. Roles and custom permissions above are read-only.
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};