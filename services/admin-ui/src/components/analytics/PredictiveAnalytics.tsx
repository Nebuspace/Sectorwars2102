import React from 'react';
import './predictive-analytics.css';

/**
 * Honesty: GET /api/v1/admin/analytics/predictions is not implemented.
 * admin_reports exposes metrics, templates, generate, analytics/export, and
 * performance/metrics only. Do not invent timeframe selectors, forecast charts,
 * or risk-factor panels.
 */
export const PredictiveAnalytics: React.FC = () => {
  return (
    <div className="predictive-analytics">
      <div className="analytics-header">
        <h2>Predictive Analytics — unavailable</h2>
      </div>
      <div
        role="note"
        style={{
          margin: '0 0 16px 0',
          padding: '10px 12px',
          background: 'rgba(234, 179, 8, 0.12)',
          border: '1px solid rgba(234, 179, 8, 0.35)',
          borderRadius: '6px',
          color: '#fbbf24',
          fontSize: '0.82rem',
          lineHeight: 1.4,
        }}
      >
        Predictive analytics is unavailable:{' '}
        <code style={{ color: '#fde68a' }}>/api/v1/admin/analytics/predictions</code>{' '}
        is not implemented. Admin analytics serves reports, export, and performance
        metrics only. This tab does not invent timeframe controls, forecast charts,
        or risk-factor panels.
      </div>
    </div>
  );
};

export default PredictiveAnalytics;
