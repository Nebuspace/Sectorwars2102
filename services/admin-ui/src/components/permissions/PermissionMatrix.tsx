import React from 'react';
import './permission-matrix.css';

interface PermissionMatrixProps {
  onPermissionChange?: (roleId: string, permissionId: string, granted: boolean) => void;
}

// RBAC roles/permissions are design-only (ADR-0027 / ADR-0058). There are no
// backend endpoints (/api/admin/roles, /api/admin/permissions do not exist),
// so this component renders an honest design-only banner instead of a
// fabricated permission matrix. The props interface is kept so existing
// composition (PermissionsDashboard) continues to compile; onPermissionChange
// is never invoked because there is nothing real to change.
export const PermissionMatrix: React.FC<PermissionMatrixProps> = () => {
  return (
    <div className="permission-matrix">
      <div className="matrix-header">
        <h2>Permission Matrix — design-only</h2>
      </div>
      <div className="alert alert-warning">
        <span className="alert-icon">⚠️</span>
        <span className="alert-message">
          RBAC roles are design-only (ADR-0027 / ADR-0058) — no backend endpoints
        </span>
      </div>
    </div>
  );
};
