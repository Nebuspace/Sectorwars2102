import React from 'react';
import './role-management.css';

interface RoleManagementProps {
  onRoleUpdate?: (role: unknown) => void;
}

/**
 * Honesty: RBAC role/permission management is design-only (ADR-0027 / ADR-0058).
 * /api/v1/admin/roles and /api/v1/admin/permissions do not exist. Do not invent
 * Create / Edit / Delete / Save chrome or an empty searchable role list.
 */
export const RoleManagement: React.FC<RoleManagementProps> = () => {
  const ROLES_ENDPOINT = 'GET/POST /api/v1/admin/roles';
  const PERMS_ENDPOINT = 'GET /api/v1/admin/permissions';

  return (
    <div className="role-management">
      <div className="role-header">
        <h2>Role Management — unavailable</h2>
      </div>

      <div
        role="note"
        style={{
          margin: '12px 0', padding: '12px 14px',
          background: 'rgba(234, 179, 8, 0.12)', border: '1px solid rgba(234, 179, 8, 0.35)',
          borderRadius: '6px', color: '#fbbf24', fontSize: '0.85rem', lineHeight: 1.45
        }}
      >
        RBAC role management is design-only (ADR-0027 / ADR-0058): the backend endpoints{' '}
        <code style={{ color: '#fde68a' }}>{ROLES_ENDPOINT}</code> and{' '}
        <code style={{ color: '#fde68a' }}>{PERMS_ENDPOINT}</code> are not implemented.
        This panel does not invent Create Role / Edit / Delete / Save controls or an empty role catalog.
      </div>
    </div>
  );
};
