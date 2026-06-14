import React, { useState, useEffect } from 'react';
import './role-management.css';

interface Permission {
  id: string;
  name: string;
  resource: string;
  action: string;
  description: string;
}

interface Role {
  id: string;
  name: string;
  description: string;
  permissions: string[];
  userCount: number;
  isSystem: boolean;
  createdAt: string;
  updatedAt: string;
}

interface RoleManagementProps {
  onRoleUpdate?: (role: Role) => void;
}

export const RoleManagement: React.FC<RoleManagementProps> = ({ onRoleUpdate }) => {
  const [roles, setRoles] = useState<Role[]>([]);
  const [permissions, setPermissions] = useState<Permission[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedRole, setSelectedRole] = useState<Role | null>(null);
  const [editingRole, setEditingRole] = useState<Role | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');

  useEffect(() => {
    fetchRolesAndPermissions();
  }, []);

  const fetchRolesAndPermissions = async () => {
    setLoading(true);
    setError(null);
    try {
      const [rolesResponse, permissionsResponse] = await Promise.all([
        fetch('/api/v1/admin/roles', {
          headers: {
            'Authorization': `Bearer ${localStorage.getItem('accessToken')}`
          }
        }),
        fetch('/api/v1/admin/permissions', {
          headers: {
            'Authorization': `Bearer ${localStorage.getItem('accessToken')}`
          }
        })
      ]);

      if (rolesResponse.ok && permissionsResponse.ok) {
        const rolesData = await rolesResponse.json();
        const permissionsData = await permissionsResponse.json();
        setRoles(rolesData.roles ?? []);
        setPermissions(permissionsData.permissions ?? []);
      } else {
        setRoles([]);
        setPermissions([]);
        setError('Failed to load roles and permissions — /api/v1/admin/roles and /api/v1/admin/permissions are not implemented.');
      }
    } catch (error) {
      console.error('Error fetching roles and permissions:', error);
      setRoles([]);
      setPermissions([]);
      setError('Failed to load roles and permissions. Please check your connection and try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleCreateRole = () => {
    setEditingRole({
      id: '',
      name: '',
      description: '',
      permissions: [],
      userCount: 0,
      isSystem: false,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString()
    });
    setShowCreateModal(true);
  };

  // RBAC role create/edit is design-only (ADR-0027 / ADR-0058) — there is no
  // backend (/api/v1/admin/roles does not exist). The Create/Save buttons are
  // disabled, but guard the handlers too so no dead write call is ever fired.
  const handleSaveRole = () => {
    setError('Role create/edit is design-only (ADR-0027 / ADR-0058) — no backend endpoint exists.');
  };

  const handleDeleteRole = (_roleId: string) => {
    setError('Role deletion is design-only (ADR-0027 / ADR-0058) — no backend endpoint exists.');
  };

  const togglePermission = (permissionId: string) => {
    if (!editingRole) return;

    const newPermissions = editingRole.permissions.includes(permissionId)
      ? editingRole.permissions.filter(p => p !== permissionId)
      : [...editingRole.permissions, permissionId];

    setEditingRole({
      ...editingRole,
      permissions: newPermissions
    });
  };

  const groupPermissionsByResource = () => {
    const grouped: Record<string, Permission[]> = {};
    permissions.forEach(permission => {
      if (!grouped[permission.resource]) {
        grouped[permission.resource] = [];
      }
      grouped[permission.resource].push(permission);
    });
    return grouped;
  };

  const filteredRoles = roles.filter(role => 
    role.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    role.description.toLowerCase().includes(searchTerm.toLowerCase())
  );

  if (loading) {
    return (
      <div className="role-management-loading">
        <i className="fas fa-spinner fa-spin"></i>
        <span>Loading roles and permissions...</span>
      </div>
    );
  }

  return (
    <div className="role-management">
      <div className="alert alert-warning">
        <span className="alert-icon">⚠️</span>
        <span className="alert-message">
          RBAC roles are design-only (ADR-0027 / ADR-0058) — no backend endpoints
        </span>
      </div>

      {error && (
        <div className="error-state" style={{ padding: '16px', margin: '16px 0', backgroundColor: 'rgba(231, 76, 60, 0.1)', border: '1px solid rgba(231, 76, 60, 0.3)', borderRadius: '8px', color: '#e74c3c' }}>
          <i className="fas fa-exclamation-circle" style={{ marginRight: '8px' }}></i>
          {error}
        </div>
      )}

      <div className="role-header">
        <h2>Role Management</h2>
        <div className="role-actions">
          <div className="search-box">
            <i className="fas fa-search"></i>
            <input
              type="text"
              placeholder="Search roles..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
          </div>
          <button
            className="btn btn-primary"
            onClick={handleCreateRole}
            disabled
            title="Role creation is design-only (ADR-0027 / ADR-0058) — no backend endpoint"
          >
            <i className="fas fa-plus"></i>
            Create Role
          </button>
        </div>
      </div>

      <div className="role-content">
        <div className="role-list">
          <h3>Roles ({filteredRoles.length})</h3>
          {filteredRoles.map(role => (
            <div
              key={role.id}
              className={`role-item ${selectedRole?.id === role.id ? 'selected' : ''} ${role.isSystem ? 'system' : ''}`}
              onClick={() => setSelectedRole(role)}
            >
              <div className="role-info">
                <div className="role-name">
                  {role.name}
                  {role.isSystem && <span className="system-badge">System</span>}
                </div>
                <div className="role-description">{role.description}</div>
                <div className="role-meta">
                  <span className="user-count">
                    <i className="fas fa-users"></i>
                    {role.userCount} users
                  </span>
                  <span className="permission-count">
                    <i className="fas fa-key"></i>
                    {role.permissions.length === 1 && role.permissions[0] === '*' 
                      ? 'All permissions' 
                      : `${role.permissions.length} permissions`}
                  </span>
                </div>
              </div>
              <div className="role-actions">
                <button
                  className="btn-icon"
                  onClick={(e) => {
                    e.stopPropagation();
                    setEditingRole(role);
                    setShowCreateModal(true);
                  }}
                  disabled={role.isSystem}
                  title={role.isSystem ? 'System roles cannot be edited' : 'Edit role'}
                >
                  <i className="fas fa-edit"></i>
                </button>
                <button
                  className="btn-icon delete"
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDeleteRole(role.id);
                  }}
                  disabled
                  title="Role deletion is design-only (ADR-0027 / ADR-0058) — no backend endpoint"
                >
                  <i className="fas fa-trash"></i>
                </button>
              </div>
            </div>
          ))}
        </div>

        {selectedRole && (
          <div className="role-details">
            <h3>Role Details: {selectedRole.name}</h3>
            <div className="role-permissions">
              <h4>Permissions</h4>
              {selectedRole.permissions.length === 1 && selectedRole.permissions[0] === '*' ? (
                <div className="all-permissions">
                  <i className="fas fa-infinity"></i>
                  <span>This role has all permissions</span>
                </div>
              ) : (
                <div className="permission-list">
                  {selectedRole.permissions.length === 0 ? (
                    <div className="no-permissions">No permissions assigned</div>
                  ) : (
                    selectedRole.permissions.map(permId => {
                      const permission = permissions.find(p => p.id === permId);
                      return permission ? (
                        <div key={permId} className="permission-item">
                          <i className="fas fa-check"></i>
                          <div className="permission-details">
                            <span className="permission-name">{permission.name}</span>
                            <span className="permission-description">{permission.description}</span>
                          </div>
                        </div>
                      ) : null;
                    })
                  )}
                </div>
              )}
            </div>

            <div className="role-metadata">
              <div className="metadata-item">
                <span className="label">Created:</span>
                <span className="value">{new Date(selectedRole.createdAt).toLocaleString()}</span>
              </div>
              <div className="metadata-item">
                <span className="label">Last Updated:</span>
                <span className="value">{new Date(selectedRole.updatedAt).toLocaleString()}</span>
              </div>
            </div>
          </div>
        )}
      </div>

      {showCreateModal && editingRole && (
        <div className="role-modal" onClick={() => setShowCreateModal(false)}>
          <div className="role-modal-content" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3>{editingRole.id ? 'Edit Role' : 'Create New Role'}</h3>
              <button className="close-button" onClick={() => setShowCreateModal(false)}>
                <i className="fas fa-times"></i>
              </button>
            </div>

            <div className="modal-body">
              <div className="form-group">
                <label>Role Name</label>
                <input
                  type="text"
                  value={editingRole.name}
                  onChange={(e) => setEditingRole({ ...editingRole, name: e.target.value })}
                  placeholder="e.g., Content Moderator"
                  disabled={editingRole.isSystem}
                />
              </div>

              <div className="form-group">
                <label>Description</label>
                <textarea
                  value={editingRole.description}
                  onChange={(e) => setEditingRole({ ...editingRole, description: e.target.value })}
                  placeholder="Describe the purpose of this role"
                  rows={3}
                  disabled={editingRole.isSystem}
                />
              </div>

              <div className="form-group">
                <label>Permissions</label>
                <div className="permission-groups">
                  {Object.entries(groupPermissionsByResource()).map(([resource, perms]) => (
                    <div key={resource} className="permission-group">
                      <h4>{resource.charAt(0).toUpperCase() + resource.slice(1)}</h4>
                      {perms.map(permission => (
                        <label key={permission.id} className="permission-checkbox">
                          <input
                            type="checkbox"
                            checked={editingRole.permissions.includes(permission.id)}
                            onChange={() => togglePermission(permission.id)}
                            disabled={editingRole.isSystem}
                          />
                          <div className="permission-label">
                            <span className="name">{permission.name}</span>
                            <span className="description">{permission.description}</span>
                          </div>
                        </label>
                      ))}
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={() => setShowCreateModal(false)}>
                Cancel
              </button>
              <button
                className="btn btn-primary"
                onClick={handleSaveRole}
                disabled
                title="Saving roles is design-only (ADR-0027 / ADR-0058) — no backend endpoint"
              >
                {editingRole.id ? 'Save Changes' : 'Create Role'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};