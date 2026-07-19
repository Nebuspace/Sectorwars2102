import React, { useState } from 'react';
import { ConversationDetail } from '../../types/firstLogin';

interface ConversationDetailModalProps {
  conversation: ConversationDetail | null;
  onClose: () => void;
  onExport?: (conversation: ConversationDetail) => void;
}

export const ConversationDetailModal: React.FC<ConversationDetailModalProps> = ({
  conversation,
  onClose,
  onExport
}) => {
  const [expandedPrompts, setExpandedPrompts] = useState<Set<string>>(new Set());

  if (!conversation) return null;

  const { session, exchanges, guard_personality } = conversation;

  const togglePrompt = (exchangeId: string) => {
    setExpandedPrompts(prev => {
      const newSet = new Set(prev);
      if (newSet.has(exchangeId)) {
        newSet.delete(exchangeId);
      } else {
        newSet.add(exchangeId);
      }
      return newSet;
    });
  };

  const handleExport = () => {
    if (onExport && conversation) {
      onExport(conversation);
    } else {
      // Default JSON export
      const dataStr = JSON.stringify(conversation, null, 2);
      const blob = new Blob([dataStr], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `conversation-${session.session_id}-${new Date().toISOString().split('T')[0]}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }
  };

  const formatDate = (dateStr: string | null): string => {
    if (!dateStr) return '-';
    return new Date(dateStr).toLocaleString();
  };

  const formatShipType = (shipType: string | null): string => {
    if (!shipType) return 'None';
    return shipType
      .replace(/_/g, ' ')
      .toLowerCase()
      .replace(/\b\w/g, c => c.toUpperCase());
  };

  const getMetricColor = (value: number | null): string => {
    if (value === null) return '#999';
    if (value >= 0.8) return '#22c55e'; // green
    if (value >= 0.6) return '#eab308'; // yellow
    if (value >= 0.4) return '#f97316'; // orange
    return '#ef4444'; // red
  };

  const renderMetricBar = (label: string, value: number | null) => {
    if (value === null) return null;
    const percentage = value * 100;
    return (
      <div className="metric-bar">
        <label>{label}</label>
        <div className="bar-container">
          <div
            className="bar-fill"
            style={{
              width: `${percentage}%`,
              backgroundColor: getMetricColor(value)
            }}
          />
        </div>
        <span className="metric-value">{percentage.toFixed(0)}%</span>
      </div>
    );
  };

  const renderMetricsTimeline = () => {
    // Simple SVG line chart showing metric progression
    const width = 600;
    const height = 200;
    const padding = 40;
    const chartWidth = width - padding * 2;
    const chartHeight = height - padding * 2;

    const metrics = ['persuasiveness', 'confidence', 'believability', 'current_suspicion'] as const;
    const colors = {
      persuasiveness: '#3b82f6',
      confidence: '#22c55e',
      believability: '#eab308',
      current_suspicion: '#ef4444'
    };

    const validExchanges = exchanges.filter(ex => ex.player_response);
    if (validExchanges.length === 0) return null;

    const xScale = (index: number) => padding + (index / (validExchanges.length - 1 || 1)) * chartWidth;
    const yScale = (value: number) => height - padding - value * chartHeight;

    return (
      <div className="metrics-timeline">
        <h4>📊 Analysis Metrics Over Time</h4>
        <svg width={width} height={height} className="timeline-chart">
          {/* Grid lines */}
          {[0, 0.25, 0.5, 0.75, 1].map(val => (
            <g key={val}>
              <line
                x1={padding}
                y1={yScale(val)}
                x2={width - padding}
                y2={yScale(val)}
                stroke="#e5e7eb"
                strokeWidth="1"
              />
              <text
                x={padding - 10}
                y={yScale(val) + 5}
                textAnchor="end"
                fontSize="12"
                fill="#666"
              >
                {(val * 100).toFixed(0)}%
              </text>
            </g>
          ))}

          {/* Metric lines */}
          {metrics.map(metric => {
            const points = validExchanges
              .map((ex, idx) => ({
                x: xScale(idx),
                y: ex[metric] !== null ? yScale(ex[metric]!) : null
              }))
              .filter(p => p.y !== null);

            if (points.length === 0) return null;

            const pathData = points
              .map((p, idx) => `${idx === 0 ? 'M' : 'L'} ${p.x} ${p.y}`)
              .join(' ');

            return (
              <g key={metric}>
                <path
                  d={pathData}
                  fill="none"
                  stroke={colors[metric]}
                  strokeWidth="2"
                />
                {points.map((p, idx) => (
                  <circle
                    key={idx}
                    cx={p.x}
                    cy={p.y!}
                    r="4"
                    fill={colors[metric]}
                  />
                ))}
              </g>
            );
          })}

          {/* X-axis labels */}
          {validExchanges.map((ex, idx) => (
            <text
              key={idx}
              x={xScale(idx)}
              y={height - padding + 20}
              textAnchor="middle"
              fontSize="12"
              fill="#666"
            >
              Q{ex.sequence_number}
            </text>
          ))}
        </svg>
        <div className="timeline-legend">
          {metrics.map(metric => (
            <div key={metric} className="legend-item">
              <span
                className="legend-color"
                style={{ backgroundColor: colors[metric] }}
              />
              <span className="legend-label">{metric.replace('_', ' ')}</span>
            </div>
          ))}
        </div>
      </div>
    );
  };

  return (
    <div className="conversation-modal" onClick={onClose}>
      <div className="conversation-modal-content" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="modal-header">
          <div className="header-left">
            <h2>Conversation Details</h2>
            <span className="session-id">Session: {session.session_id}</span>
          </div>
          <div className="header-actions">
            <button className="btn btn-secondary" onClick={handleExport}>
              <i className="fas fa-download"></i> Export
            </button>
            <button className="close-button" onClick={onClose}>
              <i className="fas fa-times"></i>
            </button>
          </div>
        </div>

        <div className="modal-body">
          {/* In-progress banner */}
          {!session.completed_at && (
            <div className="in-progress-banner">
              ⏳ This conversation is still in progress
            </div>
          )}

          {/* Session summary */}
          <div className="session-summary">
            <div className="summary-grid">
              <div className="summary-item">
                <label>Player</label>
                <span>{session.player_username}</span>
              </div>
              <div className="summary-item">
                <label>Started</label>
                <span>{formatDate(session.started_at)}</span>
              </div>
              <div className="summary-item">
                <label>Completed</label>
                <span>{formatDate(session.completed_at)}</span>
              </div>
              <div className="summary-item">
                <label>Ship Claimed</label>
                <span>{formatShipType(session.ship_claimed)}</span>
              </div>
              <div className="summary-item">
                <label>Ship Awarded</label>
                <span>{session.awarded_ship ? formatShipType(session.awarded_ship) : 'Pending'}</span>
              </div>
              <div className="summary-item">
                <label>Outcome</label>
                <span className={`outcome-badge outcome-${session.outcome?.toLowerCase() || 'unknown'}`}>
                  {session.outcome || 'Unknown'}
                </span>
              </div>
              <div className="summary-item">
                <label>Persuasion Score</label>
                <span>{session.final_persuasion_score?.toFixed(2) || 'N/A'}</span>
              </div>
              <div className="summary-item">
                <label>Negotiation Skill</label>
                <span>{session.negotiation_skill || 'N/A'}</span>
              </div>
              <div className="summary-item">
                <label>Total Questions</label>
                <span>{session.total_questions}</span>
              </div>
              <div className="summary-item">
                <label>Total Cost</label>
                <span className={session.total_cost_usd > 0.10 ? 'cost-high' : 'cost-normal'}>
                  ${session.total_cost_usd.toFixed(4)}
                  {session.total_cost_usd > 0.10 && ' ⚠️'}
                </span>
              </div>
            </div>
          </div>

          {/* Guard personality */}
          <div className="guard-personality-card">
            <h3>🛡️ Guard Personality</h3>
            <div className="personality-content">
              <div className="personality-header">
                <h4>{guard_personality.name}</h4>
                <span className="personality-title">{guard_personality.title}</span>
              </div>
              <p className="personality-trait">
                <strong>Trait:</strong> {guard_personality.trait}
              </p>
              <p className="personality-description">
                {guard_personality.description}
              </p>
              <div className="suspicion-meter">
                <label>Base Suspicion Level</label>
                <div className="meter-bar-container">
                  <div
                    className="meter-bar-fill"
                    style={{
                      width: `${guard_personality.base_suspicion * 100}%`,
                      backgroundColor: getMetricColor(guard_personality.base_suspicion)
                    }}
                  />
                </div>
                <span className="meter-value">
                  {(guard_personality.base_suspicion * 100).toFixed(0)}%
                </span>
              </div>
            </div>
          </div>

          {/* Metrics timeline */}
          {renderMetricsTimeline()}

          {/* Dialogue exchanges */}
          <div className="dialogue-exchanges">
            <h3>💬 Conversation Timeline</h3>
            {exchanges.map((exchange) => (
              <div key={exchange.id} className="exchange-card">
                <div className="exchange-header">
                  <span className="exchange-number">Question {exchange.sequence_number}</span>
                  <span className="exchange-timestamp">{formatDate(exchange.timestamp)}</span>
                  {exchange.ai_provider && (
                    <span className={`provider-badge provider-${exchange.ai_provider}`}>
                      {exchange.ai_provider}
                    </span>
                  )}
                  {exchange.response_time_ms && (
                    <span className="response-time">
                      ⏱️ {exchange.response_time_ms}ms
                    </span>
                  )}
                  {exchange.estimated_cost_usd !== null && (
                    <span className="exchange-cost">
                      💰 ${exchange.estimated_cost_usd.toFixed(4)}
                    </span>
                  )}
                </div>

                {exchange.topic && (
                  <div className="exchange-topic">
                    <strong>Topic:</strong> {exchange.topic}
                  </div>
                )}

                <div className="exchange-dialogue">
                  <div className="npc-prompt">
                    <strong>🛡️ Guard:</strong>
                    <p>{exchange.npc_prompt}</p>
                  </div>
                  {exchange.player_response && (
                    <div className="player-response">
                      <strong>👤 Player:</strong>
                      <p>{exchange.player_response}</p>
                    </div>
                  )}
                </div>

                {/* Analysis metrics */}
                {(exchange.persuasiveness !== null ||
                  exchange.confidence !== null ||
                  exchange.believability !== null ||
                  exchange.current_suspicion !== null) && (
                  <div className="exchange-metrics">
                    <h5>📊 Analysis</h5>
                    <div className="metrics-grid">
                      {renderMetricBar('Persuasiveness', exchange.persuasiveness)}
                      {renderMetricBar('Confidence', exchange.confidence)}
                      {renderMetricBar('Believability', exchange.believability)}
                      {renderMetricBar('Suspicion', exchange.current_suspicion)}
                    </div>
                  </div>
                )}

                {/* Contradictions */}
                {exchange.detected_contradictions && exchange.detected_contradictions.length > 0 && (
                  <div className="contradictions-alert">
                    <h5>🚩 Detected Contradictions ({exchange.detected_contradictions.length})</h5>
                    <ul className="contradiction-list">
                      {exchange.detected_contradictions.map((contradiction, idx) => (
                        <li key={idx}>{contradiction}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Prompt inspector (collapsible) */}
                {exchange.ai_provider && exchange.ai_provider !== 'fallback' && (
                  <div className="prompt-inspector">
                    <button
                      className="prompt-toggle"
                      onClick={() => togglePrompt(exchange.id)}
                    >
                      <i className={`fas fa-chevron-${expandedPrompts.has(exchange.id) ? 'down' : 'right'}`}></i>
                      🔍 View AI Prompts
                    </button>
                    {expandedPrompts.has(exchange.id) && (
                      <div className="prompt-content">
                        <details open>
                          <summary>System Prompt ({(exchange.npc_prompt?.length || 0)} chars)</summary>
                          <pre className="prompt-text">
                            {exchange.npc_prompt || 'N/A'}
                          </pre>
                        </details>
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
