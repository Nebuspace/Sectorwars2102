import React, { useState, useEffect, useCallback } from 'react';
import PageHeader from '../ui/PageHeader';
import { CustomReportBuilder } from '../analytics/CustomReportBuilder';
import { PredictiveAnalytics } from '../analytics/PredictiveAnalytics';
import { PerformanceMetrics } from '../analytics/PerformanceMetrics';
import './advanced-analytics.css';

interface ReportResult {
  id: string;
  name: string;
  generatedAt: string;
  data: any;
  template: any;
}

const SAVED_TEMPLATES_KEY = 'reportTemplates';

export const AdvancedAnalytics: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'reports' | 'predictive' | 'performance' | 'export'>('reports');
  const [generatedReports, setGeneratedReports] = useState<ReportResult[]>([]);
  const [selectedReport, setSelectedReport] = useState<ReportResult | null>(null);
  const [exportFormat, setExportFormat] = useState<'csv' | 'json' | 'excel' | 'pdf'>('csv');
  const [saveMessage, setSaveMessage] = useState<string | null>(null);

  // Load saved templates from localStorage on mount
  useEffect(() => {
    try {
      const stored = localStorage.getItem(SAVED_TEMPLATES_KEY);
      if (stored) {
        const parsed = JSON.parse(stored);
        if (Array.isArray(parsed) && parsed.length > 0) {
          console.log(`Loaded ${parsed.length} saved report templates from localStorage`);
        }
      }
    } catch (e) {
      console.warn('Failed to load saved templates:', e);
    }
  }, []);

  const handleSaveTemplate = useCallback((template: any) => {
    try {
      const existingRaw = localStorage.getItem(SAVED_TEMPLATES_KEY);
      const existing = existingRaw ? JSON.parse(existingRaw) : [];
      const savedTemplate = {
        ...template,
        id: `saved-${Date.now()}`,
        savedAt: new Date().toISOString()
      };
      existing.push(savedTemplate);
      localStorage.setItem(SAVED_TEMPLATES_KEY, JSON.stringify(existing));
      setSaveMessage(`Template "${template.name}" saved successfully!`);
      setTimeout(() => setSaveMessage(null), 3000);
    } catch (e) {
      console.error('Failed to save template:', e);
      setSaveMessage('Failed to save template. Storage may be full.');
      setTimeout(() => setSaveMessage(null), 3000);
    }
  }, []);

  const handleGenerateReport = async (template: any) => {
    try {
      const response = await fetch('/api/v1/admin/reports/generate', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${localStorage.getItem('accessToken')}`
        },
        body: JSON.stringify(template)
      });

      if (!response.ok) {
        setSaveMessage(
          response.status === 404
            ? 'Failed to generate report \u2014 /api/v1/admin/reports/generate endpoint not implemented (404)'
            : `Failed to generate report \u2014 request failed (HTTP ${response.status})`
        );
        setTimeout(() => setSaveMessage(null), 6000);
        return;
      }

      const report = await response.json();
      setGeneratedReports([report, ...generatedReports]);
      setSelectedReport(report);
      setSaveMessage(`Report "${template.name}" generated successfully!`);
      setTimeout(() => setSaveMessage(null), 3000);
    } catch (error) {
      console.error('Error generating report:', error);
      setSaveMessage('Failed to generate report \u2014 gameserver unreachable (network error)');
      setTimeout(() => setSaveMessage(null), 6000);
    }
  };

  const exportOptions = [
    { id: 'players', name: 'Player Data', description: 'Export all player information including stats and activity' },
    { id: 'economy', name: 'Economy Data', description: 'Export transaction history and market data' },
    { id: 'combat', name: 'Combat Logs', description: 'Export combat encounters and battle statistics' },
    { id: 'teams', name: 'Team Data', description: 'Export team information and alliance data' },
    { id: 'ships', name: 'Fleet Data', description: 'Export ship information and fleet statistics' },
    { id: 'performance', name: 'Performance Metrics', description: 'Export system performance and optimization data' }
  ];

  return (
    <div className="advanced-analytics">
      <PageHeader
        title="Advanced Analytics"
        subtitle="Generate custom reports, view predictions, and export data"
      />

      <div className="analytics-tabs">
        <button
          className={`tab ${activeTab === 'reports' ? 'active' : ''}`}
          onClick={() => setActiveTab('reports')}
        >
          <i className="fas fa-file-alt"></i>
          Custom Reports
        </button>
        <button
          className={`tab ${activeTab === 'predictive' ? 'active' : ''}`}
          onClick={() => setActiveTab('predictive')}
        >
          <i className="fas fa-chart-line"></i>
          Predictive Analytics
        </button>
        <button
          className={`tab ${activeTab === 'performance' ? 'active' : ''}`}
          onClick={() => setActiveTab('performance')}
        >
          <i className="fas fa-tachometer-alt"></i>
          Performance
        </button>
        <button
          className={`tab ${activeTab === 'export' ? 'active' : ''}`}
          onClick={() => setActiveTab('export')}
        >
          <i className="fas fa-download"></i>
          Data Export
        </button>
      </div>

      <div className="analytics-content">
        {activeTab === 'reports' && (
          <div className="reports-section">
            <div className="reports-builder">
              {saveMessage && (
                <div style={{
                  padding: '10px 16px',
                  marginBottom: '12px',
                  borderRadius: '6px',
                  background: saveMessage.includes('Failed') ? '#7f1d1d' : '#14532d',
                  color: saveMessage.includes('Failed') ? '#fca5a5' : '#86efac',
                  border: `1px solid ${saveMessage.includes('Failed') ? '#991b1b' : '#166534'}`,
                  fontSize: '14px'
                }}>
                  {saveMessage}
                </div>
              )}
              <CustomReportBuilder
                onGenerate={handleGenerateReport}
                onSave={handleSaveTemplate}
              />
            </div>
            
            {generatedReports.length > 0 && (
              <div className="generated-reports">
                <h3>Generated Reports</h3>
                <div className="reports-list">
                  {generatedReports.map(report => (
                    <div
                      key={report.id}
                      className={`report-item ${selectedReport?.id === report.id ? 'selected' : ''}`}
                      onClick={() => setSelectedReport(report)}
                    >
                      <div className="report-header">
                        <h4>{report.name}</h4>
                        <span className="report-time">
                          {new Date(report.generatedAt).toLocaleString()}
                        </span>
                      </div>
                      <div className="report-actions">
                        <button className="btn-icon" title="Download">
                          <i className="fas fa-download"></i>
                        </button>
                        <button className="btn-icon" title="Share">
                          <i className="fas fa-share"></i>
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {activeTab === 'predictive' && (
          <PredictiveAnalytics />
        )}

        {activeTab === 'performance' && (
          <PerformanceMetrics />
        )}

        {activeTab === 'export' && (
          <div className="export-section">
            <div className="export-header">
              <h2>Data Export Center</h2>
              <p>Export your game data in various formats for external analysis</p>
            </div>

            <div className="alert alert-warning">
              <span className="alert-icon">⚠️</span>
              <span className="alert-message">
                Data export endpoint offline — /api/v1/admin/analytics/export not implemented. Export is disabled.
              </span>
            </div>

            <div className="export-format">
              <h3>Select Export Format</h3>
              <div className="format-options">
                <label className={`format-option ${exportFormat === 'csv' ? 'selected' : ''}`}>
                  <input
                    type="radio"
                    value="csv"
                    checked={exportFormat === 'csv'}
                    onChange={(e) => setExportFormat(e.target.value as any)}
                  />
                  <i className="fas fa-file-csv"></i>
                  <span>CSV</span>
                  <small>Comma-separated values</small>
                </label>
                <label className={`format-option ${exportFormat === 'json' ? 'selected' : ''}`}>
                  <input
                    type="radio"
                    value="json"
                    checked={exportFormat === 'json'}
                    onChange={(e) => setExportFormat(e.target.value as any)}
                  />
                  <i className="fas fa-file-code"></i>
                  <span>JSON</span>
                  <small>JavaScript Object Notation</small>
                </label>
                <label className={`format-option ${exportFormat === 'excel' ? 'selected' : ''}`}>
                  <input
                    type="radio"
                    value="excel"
                    checked={exportFormat === 'excel'}
                    onChange={(e) => setExportFormat(e.target.value as any)}
                  />
                  <i className="fas fa-file-excel"></i>
                  <span>Excel</span>
                  <small>Microsoft Excel format</small>
                </label>
                <label className={`format-option ${exportFormat === 'pdf' ? 'selected' : ''}`}>
                  <input
                    type="radio"
                    value="pdf"
                    checked={exportFormat === 'pdf'}
                    onChange={(e) => setExportFormat(e.target.value as any)}
                  />
                  <i className="fas fa-file-pdf"></i>
                  <span>PDF</span>
                  <small>Portable Document Format</small>
                </label>
              </div>
            </div>

            <div className="export-options">
              <h3>Available Data Exports</h3>
              <div className="export-grid">
                {exportOptions.map(option => (
                  <div key={option.id} className="export-card">
                    <div className="export-icon">
                      <i className={`fas fa-${
                        option.id === 'players' ? 'users' :
                        option.id === 'economy' ? 'chart-line' :
                        option.id === 'combat' ? 'swords' :
                        option.id === 'teams' ? 'user-friends' :
                        option.id === 'ships' ? 'rocket' :
                        'chart-bar'
                      }`}></i>
                    </div>
                    <div className="export-info">
                      <h4>{option.name}</h4>
                      <p>{option.description}</p>
                    </div>
                    <button
                      className="btn btn-primary"
                      disabled
                      title="Export endpoint offline — /api/v1/admin/analytics/export not implemented"
                    >
                      <i className="fas fa-download"></i>
                      Export
                    </button>
                  </div>
                ))}
              </div>
            </div>

          </div>
        )}
      </div>
    </div>
  );
};