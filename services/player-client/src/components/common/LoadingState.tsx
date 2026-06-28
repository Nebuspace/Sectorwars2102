import React from 'react';
import './loading-state.css';

interface LoadingStateProps {
  message?: string;
}

const LoadingState: React.FC<LoadingStateProps> = ({ message = 'Loading...' }) => {
  return (
    <div className="loading-state-container" role="status" aria-live="polite">
      <div className="loading-state-spinner" aria-hidden="true"></div>
      <p className="loading-state-message">{message}</p>
    </div>
  );
};

export default LoadingState;
