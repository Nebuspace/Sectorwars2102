import React from 'react';
import './permission-matrix.css';

interface PermissionMatrixProps {
  onPermissionChange?: (roleId: string, permissionId: string, granted: boolean) => void;
}

// Orphaned / unused matrix banner UI. RBAC scopes are shipped and live
// elsewhere (ADR-0027 / ADR-0058); this component is not wired to them and
// only renders a placeholder banner. The props interface is kept so existing
// composition (PermissionsDashboard) continues to compile; onPermissionChange
// is never invoked because this UI is unused.
export const PermissionMatrix: React.FC<PermissionMatrixProps> = () => {
  return (
    <div className="permission-matrix">
      <div className="matrix-header">
        <h2>Permission Matrix — unused UI</h2>
      </div>
      <div className="alert alert-warning">
        <span className="alert-icon">⚠️</span>
        <span className="alert-message">
          This matrix UI is unused/orphaned — RBAC scopes live elsewhere (ADR-0027 / ADR-0058)
        </span>
      </div>
    </div>
  );
};
