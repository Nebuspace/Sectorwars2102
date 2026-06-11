import React from 'react';
import './empty-state.css';

interface EmptyStateProps {
  icon: string;
  title: string;
  message: string;
  action?: {
    label: string;
    onClick: () => void;
  };
  /** Optional extra content (e.g. multiple action buttons) rendered below the message */
  children?: React.ReactNode;
}

const EmptyState: React.FC<EmptyStateProps> = ({ icon, title, message, action, children }) => {
  return (
    <div className="empty-state-container">
      <span className="empty-state-icon" aria-hidden="true">{icon}</span>
      <h2 className="empty-state-title">{title}</h2>
      <p className="empty-state-message">{message}</p>
      {action && (
        <button type="button" className="empty-state-action" onClick={action.onClick}>
          {action.label}
        </button>
      )}
      {children && <div className="empty-state-extra">{children}</div>}
    </div>
  );
};

export default EmptyState;
