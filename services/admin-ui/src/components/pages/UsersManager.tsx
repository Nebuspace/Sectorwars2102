import React, { useState, useEffect, FormEvent, ChangeEvent, useRef } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import { useAdmin } from '../../contexts/AdminContext';
import PageHeader from '../ui/PageHeader';
import { api } from '../../utils/auth';

// Types
interface User {
  id: string;
  username: string;
  email: string | null;
  is_active: boolean;
  is_admin: boolean;
  created_at: string;
  last_login: string | null;
}

/**
 * Normalize a FastAPI error into a renderable string. `detail` is a plain
 * string for HTTPException errors but an array of {loc, msg, type} objects
 * for 422 validation errors — rendering the raw value would break React.
 */
const extractErrorDetail = (err: any, fallback: string): string => {
  const detail = err?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item: any) => (typeof item?.msg === 'string' ? item.msg : JSON.stringify(item)))
      .join('; ');
  }
  return err?.message || fallback;
};

/**
 * NPC filler-account detection (v1 heuristic, client-side).
 *
 * Pattern-matches the `npc_filler_<n>` usernames and `@dev.local` emails
 * observed in seeded dev data. This is observed-data matching only — no
 * backend account-kind field exists yet, and nothing formally reserves
 * either format. If a real `kind`/`is_npc` flag is ever added to the user
 * schema, replace this heuristic with it.
 */
const isNpcAccount = (user: Pick<User, 'username' | 'email'>): boolean =>
  user.username.startsWith('npc_filler_') ||
  (user.email?.endsWith('@dev.local') ?? false);

const UsersManager: React.FC = () => {
  const { user: currentUser } = useAuth();
  const { users, loadUsers, isLoading, error: contextError } = useAdmin();
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const hasLoaded = useRef(false);
  const [editMode, setEditMode] = useState<boolean>(false);
  const [selectedUser, setSelectedUser] = useState<User | null>(null);
  const [showCreateModal, setShowCreateModal] = useState<boolean>(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState<boolean>(false);
  const [confirmUsername, setConfirmUsername] = useState<string>('');
  const [showResetModal, setShowResetModal] = useState<boolean>(false);
  const [resetPassword, setResetPassword] = useState<string>('');

  // Search/filter state
  const [searchTerm, setSearchTerm] = useState<string>('');
  const [includeNpc, setIncludeNpc] = useState<boolean>(true);

  // Form states for new user
  const [newUsername, setNewUsername] = useState<string>('');
  const [newEmail, setNewEmail] = useState<string>('');

  // Form states for edit user
  const [editUsername, setEditUsername] = useState<string>('');
  const [editEmail, setEditEmail] = useState<string>('');
  const [editIsActive, setEditIsActive] = useState<boolean>(true);

  // Load users when component mounts (only once)
  useEffect(() => {
    if (currentUser && currentUser.is_admin && !hasLoaded.current && users.length === 0) {
      hasLoaded.current = true;
      loadUsers();
    }
  }, [currentUser?.is_admin, users.length]); // Also check if users are already loaded

  // Escape closes the create modal (lightweight a11y; full trap = Scopes pattern later)
  useEffect(() => {
    if (!showCreateModal) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setShowCreateModal(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [showCreateModal]);

  // Handle create user form submission
  const handleCreateUser = async (e: FormEvent) => {
    e.preventDefault();

    try {
      setError(null);
      setSuccessMessage(null);

      // POST /api/v1/users/ — non-admin account only. Admin-hood is grant-only
      // via Admin → Scopes (POST /api/v1/admin/scopes/grant).
      await api.post('/api/v1/users/', {
        username: newUsername,
        email: newEmail || null
      });

      // Reset form
      setNewUsername('');
      setNewEmail('');
      setShowCreateModal(false);
      setSuccessMessage(`User "${newUsername}" created successfully.`);

      // Refresh users list
      loadUsers();
    } catch (err: any) {
      console.error('Error creating user:', err);
      setError(extractErrorDetail(err, 'Failed to create user'));
    }
  };

  // Handle edit user click
  const handleEditClick = (user: User) => {
    setSelectedUser(user);
    setEditUsername(user.username);
    setEditEmail(user.email || '');
    setEditIsActive(user.is_active);
    setEditMode(true);
  };

  // Handle save edit form submission
  const handleSaveEdit = async (e: FormEvent) => {
    e.preventDefault();
    
    if (!selectedUser) return;
    
    try {
      setError(null);
      setSuccessMessage(null);

      // PUT /api/v1/users/{id} (users.py:156) — partial update; is_active
      // doubles as the activate/deactivate control (no dedicated endpoints exist).
      const updateData = {
        username: editUsername,
        email: editEmail || null,
        is_active: editIsActive
      };

      await api.put(`/api/v1/users/${selectedUser.id}`, updateData);

      // Reset edit state
      setEditMode(false);
      setSelectedUser(null);
      setSuccessMessage(`User "${editUsername}" updated successfully.`);

      // Refresh users list
      loadUsers();
    } catch (err: any) {
      console.error('Error updating user:', err);
      setError(extractErrorDetail(err, 'Failed to update user'));
    }
  };

  // Handle delete user click
  const handleDeleteClick = (user: User) => {
    setSelectedUser(user);
    setConfirmUsername('');
    setShowDeleteConfirm(true);
  };

  // Handle confirm delete
  const handleConfirmDelete = async () => {
    if (!selectedUser || confirmUsername !== selectedUser.username) {
      return;
    }
    
    try {
      setError(null);
      setSuccessMessage(null);

      // DELETE /api/v1/users/{id} (users.py:210) — soft delete (sets deleted=true).
      await api.delete(`/api/v1/users/${selectedUser.id}`);

      // Reset delete state
      setShowDeleteConfirm(false);
      setSelectedUser(null);
      setConfirmUsername('');
      setSuccessMessage(`User "${selectedUser.username}" deleted.`);

      // Refresh users list
      loadUsers();
    } catch (err: any) {
      console.error('Error deleting user:', err);
      setError(extractErrorDetail(err, 'Failed to delete user'));
    }
  };

  // Handle reset password click — opens the modal (admin accounts only:
  // PUT /users/{id}/password 404s for non-admin users by design).
  const handleResetClick = (user: User) => {
    setSelectedUser(user);
    setResetPassword('');
    setShowResetModal(true);
  };

  // Handle reset password form submission
  const handleResetPassword = async (e: FormEvent) => {
    e.preventDefault();

    if (!selectedUser) return;

    try {
      setError(null);
      setSuccessMessage(null);

      // PUT /api/v1/users/{id}/password (users.py:242). The endpoint declares a
      // single non-embedded scalar Body param, so the request body is the raw
      // JSON-encoded password string (not an object).
      await api.put(`/api/v1/users/${selectedUser.id}/password`, JSON.stringify(resetPassword), {
        headers: { 'Content-Type': 'application/json' }
      });

      setShowResetModal(false);
      setSelectedUser(null);
      setResetPassword('');
      setSuccessMessage('Password updated successfully.');
    } catch (err: any) {
      console.error('Error resetting password:', err);
      setError(extractErrorDetail(err, 'Failed to reset password'));
    }
  };

  // Format date for display
  const formatDate = (dateString: string | null) => {
    if (!dateString) return 'Never';
    const date = new Date(dateString);
    return new Intl.DateTimeFormat('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    }).format(date);
  };

  // Filter users based on NPC toggle and search term
  const filteredUsers = users.filter(user => {
    if (!includeNpc && isNpcAccount(user)) return false;
    if (!searchTerm) return true;

    const searchLower = searchTerm.toLowerCase();
    const statusText = !user.is_active ? 'inactive' : user.is_admin ? 'admin' : 'active';
    
    return (
      user.username.toLowerCase().includes(searchLower) ||
      (user.email && user.email.toLowerCase().includes(searchLower)) ||
      statusText.includes(searchLower)
    );
  });

  // Use context error or local error, but be more specific
  const displayError = error || (contextError && users.length === 0 ? contextError : null);
  
  return (
    <div className="page-container">
      <PageHeader 
        title="User Management" 
        subtitle="Manage user accounts, permissions, and access controls"
      />
      
      <div className="page-content">
        {/* Search and Actions Header */}
        <div className="flex justify-between items-center mb-6 gap-4">
          <div className="flex items-center gap-4 flex-1">
            <div className="text-muted">
              {filteredUsers.length} of {users.length} users
              {searchTerm && (
                <span className="ml-2 text-xs">
                  (filtered by &quot;{searchTerm}&quot;)
                </span>
              )}
              {!includeNpc && (
                <span className="ml-2 text-xs">
                  (NPC accounts hidden)
                </span>
              )}
            </div>
            <div className="search-box flex-1 max-w-md">
              <input
                type="text"
                placeholder="Search users by name, email, or status..."
                className="form-input"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
              />
              {searchTerm && (
                <button
                  className="btn btn-sm btn-ghost ml-2"
                  onClick={() => setSearchTerm('')}
                  title="Clear search"
                >
                  ✕
                </button>
              )}
            </div>
            <label className="form-checkbox text-muted" title="Heuristic: matches npc_filler_* usernames / @dev.local emails observed in seeded dev data (no backend account-kind field exists yet)">
              <input
                type="checkbox"
                checked={includeNpc}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setIncludeNpc(e.target.checked)}
                className="mr-2"
              />
              Include NPC accounts
            </label>
          </div>
          <button 
            className="btn btn-primary"
            onClick={() => setShowCreateModal(true)}
          >
            Create User
          </button>
        </div>
      
        {displayError && (
          <div className="alert alert-error mb-6">
            <p>{displayError}</p>
            <button className="btn btn-sm btn-outline" onClick={() => setError(null)}>Dismiss</button>
          </div>
        )}

        {successMessage && (
          <div className="alert alert-success mb-6">
            <p>{successMessage}</p>
            <button className="btn btn-sm btn-outline" onClick={() => setSuccessMessage(null)}>Dismiss</button>
          </div>
        )}
        
        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <div className="loading-spinner mr-3"></div>
            <p className="text-muted">Loading users...</p>
          </div>
        ) : (
          <div className="card">
            <div className="table-container">
              <table className="table">
                <thead>
                  <tr>
                    <th>Username</th>
                    <th>Email</th>
                    <th>Status</th>
                    <th>Created</th>
                    <th>Last Login</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredUsers.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="text-muted text-center py-8">
                        {users.length === 0
                          ? 'No users loaded.'
                          : searchTerm
                            ? `No users match “${searchTerm}”.`
                            : 'No users to show with the current filters.'}
                      </td>
                    </tr>
                  ) : (
                  filteredUsers.map((user: User) => (
                    <tr key={user.id}>
                      <td className="font-medium">
                        {user.username || <span className="text-muted">[No Username]</span>}
                        {isNpcAccount(user) && (
                          <span className="badge badge-info ml-2" title="Matches the npc_filler_* / @dev.local pattern observed in seeded dev data (heuristic)">NPC</span>
                        )}
                      </td>
                      <td className="text-muted">{user.email || 'N/A'}</td>
                      <td>
                        <span className={`badge ${!user.is_active ? 'badge-error' : user.is_admin ? 'badge-warning' : 'badge-success'}`}>
                          {!user.is_active ? 'Inactive' : user.is_admin ? 'Admin' : 'Active'}
                        </span>
                      </td>
                      <td className="text-muted date-cell">{formatDate(user.created_at)}</td>
                      <td className="text-muted date-cell">{formatDate(user.last_login)}</td>
                      <td>
                        <div className="action-buttons">
                          <Link
                            to={`/scopes?user=${encodeURIComponent(user.id)}`}
                            className="btn btn-sm btn-outline"
                            title="Manage scopes for this user"
                          >
                            Scopes
                          </Link>
                          {/* Prevent destructive actions on current user and on protected admin account */}
                          {user.username === 'admin' ? (
                            <span className="badge badge-info">Protected Account</span>
                          ) : currentUser && user.id !== currentUser.id ? (
                            <>
                              <button 
                                className="btn btn-sm btn-outline"
                                onClick={() => handleEditClick(user)}
                                title="Edit User"
                              >
                                Edit
                              </button>
                              <button 
                                className="btn btn-sm btn-outline btn-error"
                                onClick={() => handleDeleteClick(user)}
                                title="Delete User"
                              >
                                Delete
                              </button>
                              {/* Backend only supports password resets for admin accounts
                                  (PUT /users/{id}/password 404s otherwise) */}
                              {user.is_admin && (
                                <button
                                  className="btn btn-sm btn-outline btn-warning"
                                  onClick={() => handleResetClick(user)}
                                  title="Reset Password"
                                >
                                  Reset
                                </button>
                              )}
                            </>
                          ) : (
                            <span className="badge badge-info">Current User</span>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
      
        {/* Create User Modal */}
        {showCreateModal && (
          <div
            className="modal-overlay"
            role="presentation"
            onClick={(e) => {
              if (e.target === e.currentTarget) setShowCreateModal(false);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setShowCreateModal(false);
            }}
          >
            <div
              className="modal"
              role="dialog"
              aria-modal="true"
              aria-labelledby="create-user-title"
            >
              <div className="modal-header">
                <h3 id="create-user-title" className="modal-title">Create New User</h3>
                <button
                  type="button"
                  className="btn btn-sm btn-ghost"
                  onClick={() => setShowCreateModal(false)}
                  aria-label="Close"
                >
                  ×
                </button>
              </div>
              <div className="modal-body">
                <form onSubmit={handleCreateUser} className="space-y-4">
                  <div className="form-group">
                    <label htmlFor="username" className="form-label">Username</label>
                    <input
                      id="username"
                      type="text"
                      className="form-input"
                      value={newUsername}
                      onChange={(e: ChangeEvent<HTMLInputElement>) => setNewUsername(e.target.value)}
                      required
                      minLength={3}
                      maxLength={50}
                    />
                  </div>
                  
                  <div className="form-group">
                    <label htmlFor="email" className="form-label">Email</label>
                    <input
                      id="email"
                      type="email"
                      className="form-input"
                      value={newEmail}
                      onChange={(e: ChangeEvent<HTMLInputElement>) => setNewEmail(e.target.value)}
                    />
                  </div>

                  <p className="text-muted text-xs">
                    Creates a normal account (OAuth sign-in). To grant admin
                    powers, open{' '}
                    <Link to="/scopes" className="link">
                      Admin → Scopes
                    </Link>{' '}
                    after create — there is no “make admin” toggle here.
                  </p>
                  
                  <div className="modal-footer">
                    <button type="button" className="btn btn-outline" onClick={() => setShowCreateModal(false)}>
                      Cancel
                    </button>
                    <button type="submit" className="btn btn-primary">
                      Create User
                    </button>
                  </div>
                </form>
              </div>
            </div>
          </div>
        )}
      
        {/* Edit User Modal */}
        {editMode && selectedUser && (
          <div className="modal-overlay">
            <div className="modal">
              <div className="modal-header">
                <h3 className="modal-title">Edit User: {selectedUser.username}</h3>
                <button className="btn btn-sm btn-ghost" onClick={() => {
                  setEditMode(false);
                  setSelectedUser(null);
                }}>×</button>
              </div>
              <div className="modal-body">
                <form onSubmit={handleSaveEdit} className="space-y-4">
                  <div className="form-group">
                    <label htmlFor="edit-username" className="form-label">Username</label>
                    <input
                      id="edit-username"
                      type="text"
                      className="form-input"
                      value={editUsername}
                      onChange={(e: ChangeEvent<HTMLInputElement>) => setEditUsername(e.target.value)}
                      required
                      minLength={3}
                      maxLength={50}
                    />
                  </div>
                  
                  <div className="form-group">
                    <label htmlFor="edit-email" className="form-label">Email</label>
                    <input
                      id="edit-email"
                      type="email"
                      className="form-input"
                      value={editEmail}
                      onChange={(e: ChangeEvent<HTMLInputElement>) => setEditEmail(e.target.value)}
                      required
                    />
                  </div>
                  
                  <div className="form-group">
                    <label>
                      <input
                        type="checkbox"
                        checked={editIsActive}
                        onChange={(e: ChangeEvent<HTMLInputElement>) => setEditIsActive(e.target.checked)}
                        className="form-checkbox mr-2"
                      />
                      Account Active
                    </label>
                  </div>
                  
                  <div className="modal-footer">
                    <button type="button" className="btn btn-outline" onClick={() => {
                      setEditMode(false);
                      setSelectedUser(null);
                    }}>
                      Cancel
                    </button>
                    <button type="submit" className="btn btn-primary">
                      Save Changes
                    </button>
                  </div>
                </form>
              </div>
            </div>
          </div>
        )}
      
        {/* Delete Confirmation Modal */}
        {showDeleteConfirm && selectedUser && (
          <div className="modal-overlay">
            <div className="modal">
              <div className="modal-header">
                <h3 className="modal-title">Delete User</h3>
                <button className="btn btn-sm btn-ghost" onClick={() => {
                  setShowDeleteConfirm(false);
                  setSelectedUser(null);
                }}>×</button>
              </div>
              <div className="modal-body">
                <p className="mb-4">
                  Are you sure you want to delete the user <strong>{selectedUser.username}</strong>?
                  This action cannot be undone.
                </p>
                <p className="mb-4">
                  Type the username <strong>{selectedUser.username}</strong> to confirm:
                </p>
                <input
                  type="text"
                  className="form-input"
                  value={confirmUsername}
                  onChange={(e: ChangeEvent<HTMLInputElement>) => setConfirmUsername(e.target.value)}
                />
                
                <div className="modal-footer">
                  <button type="button" className="btn btn-outline" onClick={() => {
                    setShowDeleteConfirm(false);
                    setSelectedUser(null);
                  }}>
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="btn btn-error"
                    disabled={confirmUsername !== selectedUser.username}
                    onClick={handleConfirmDelete}
                  >
                    Delete User
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Reset Password Modal */}
        {showResetModal && selectedUser && (
          <div className="modal-overlay">
            <div className="modal">
              <div className="modal-header">
                <h3 className="modal-title">Reset Password: {selectedUser.username}</h3>
                <button className="btn btn-sm btn-ghost" onClick={() => {
                  setShowResetModal(false);
                  setSelectedUser(null);
                }}>×</button>
              </div>
              <div className="modal-body">
                <form onSubmit={handleResetPassword} className="space-y-4">
                  <p className="mb-4">
                    Set a new password for the admin account <strong>{selectedUser.username}</strong>.
                  </p>
                  <div className="form-group">
                    <label htmlFor="reset-password" className="form-label">New Password</label>
                    <input
                      id="reset-password"
                      type="password"
                      className="form-input"
                      value={resetPassword}
                      onChange={(e: ChangeEvent<HTMLInputElement>) => setResetPassword(e.target.value)}
                      required
                      minLength={8}
                      autoFocus
                    />
                  </div>

                  <div className="modal-footer">
                    <button type="button" className="btn btn-outline" onClick={() => {
                      setShowResetModal(false);
                      setSelectedUser(null);
                    }}>
                      Cancel
                    </button>
                    <button type="submit" className="btn btn-warning" disabled={resetPassword.length < 8}>
                      Reset Password
                    </button>
                  </div>
                </form>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default UsersManager;