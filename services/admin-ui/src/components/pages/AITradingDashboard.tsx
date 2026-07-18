import React, { useState, useEffect, useCallback } from 'react';
import PageHeader from '../ui/PageHeader';
import { api } from '../../utils/auth';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { MarketPredictionInterface } from '../ai/MarketPredictionInterface';
import { RouteOptimizationDisplay } from '../ai/RouteOptimizationDisplay';
import { PlayerBehaviorAnalytics } from '../ai/PlayerBehaviorAnalytics';
import './ai-trading-dashboard.css';

interface AIModel {
  id: string;
  name: string;
  type: 'price_prediction' | 'route_optimization' | 'behavior_analysis';
  status: 'active' | 'training' | 'inactive' | 'error';
  accuracy: number;
  lastTrainedAt: string;
  nextTrainingAt: string;
  predictions: number;
  avgResponseTime: number;
}

interface PredictionAccuracy {
  commodity: string;
  accuracy: number;
  predictions: number;
  trend: 'improving' | 'stable' | 'declining';
}

// /api/v1/admin/ai/profiles returns only these fields. Risk tolerance,
// trading patterns, AI engagement, and profit impact were stripped from the
// payload (canon gap: ARIA per-player profile scoring is not implemented
// server-side), so the table renders em-dashes for those columns.
interface PlayerProfile {
  playerId: string;
  playerName: string;
  lastActive: string;
}

interface SystemMetrics {
  // null = no engine/telemetry exists server-side (honest absence, not zero)
  totalPredictions: number | null;
  avgAccuracy: number | null;
  activeProfiles: number;
  recommendationAcceptance: number | null;
  modelHealth: 'healthy' | 'degraded' | 'critical' | null;
  queuedJobs: number | null;
  processingRate: number | null;
}

const AITradingDashboard: React.FC = () => {
  const [models, setModels] = useState<AIModel[]>([]);
  const [predictions, setPredictions] = useState<PredictionAccuracy[]>([]);
  const [profiles, setProfiles] = useState<PlayerProfile[]>([]);
  const [metrics, setMetrics] = useState<SystemMetrics | null>(null);
  const [selectedTab, setSelectedTab] = useState<'overview' | 'models' | 'predictions' | 'profiles' | 'market-predictions' | 'route-optimization' | 'behavior-analytics'>('overview');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { isConnected, subscribe } = useWebSocket();

  // WebSocket event handlers
  const handleModelUpdate = useCallback((data: any) => {
    console.log('AI model update:', data);
    setModels(prev => prev.map(model => 
      model.id === data.modelId ? { ...model, ...data } : model
    ));
  }, []);

  const handlePredictionUpdate = useCallback((data: any) => {
    console.log('Prediction update:', data);
    // Update metrics with new prediction data
    if (metrics) {
      setMetrics({
        ...metrics,
        totalPredictions: metrics.totalPredictions != null ? metrics.totalPredictions + 1 : null
      });
    }
  }, [metrics]);

  // Subscribe to WebSocket events
  useEffect(() => {
    const unsubModel = subscribe('ai:model-update', handleModelUpdate);
    const unsubPrediction = subscribe('ai:prediction-made', handlePredictionUpdate);

    return () => {
      unsubModel();
      unsubPrediction();
    };
  }, [subscribe, handleModelUpdate, handlePredictionUpdate]);

  const fetchData = async () => {
    try {
      setLoading(true);
      setError(null);

      // Fetch AI models status
      const modelsResponse = await api.get('/api/v1/admin/ai/models');
      setModels(modelsResponse.data || []);

      // Fetch prediction accuracy
      const predictionsResponse = await api.get('/api/v1/admin/ai/predictions/accuracy');
      setPredictions(predictionsResponse.data || []);

      // Fetch player profiles
      const profilesResponse = await api.get('/api/v1/admin/ai/profiles');
      setProfiles(profilesResponse.data || []);

      // Fetch system metrics
      const metricsResponse = await api.get('/api/v1/admin/ai/metrics');
      setMetrics(metricsResponse.data);
    } catch (err: any) {
      console.error('Failed to fetch AI data:', err);
      setError(err.response?.data?.detail || 'Failed to load AI trading data');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 60000); // Refresh every minute
    return () => clearInterval(interval);
  }, []);

  const handleModelAction = async (modelId: string, action: 'start' | 'stop' | 'train') => {
    try {
      await api.post(`/api/v1/admin/ai/models/${modelId}/${action}`);
      await fetchData();
    } catch (err) {
      console.error(`Failed to ${action} model:`, err);
      alert(`Failed to ${action} model`);
    }
  };

  const getStatusColor = (status: string | null) => {
    if (status == null) return '';
    switch (status) {
      case 'active':
      case 'healthy':
        return 'status-active';
      case 'training':
        return 'status-training';
      case 'degraded':
      case 'inactive':
        return 'status-inactive';
      case 'error':
      case 'critical':
        return 'status-error';
      default:
        return '';
    }
  };

  if (loading) {
    return (
      <div className="ai-trading-dashboard loading">
        <div className="loading-spinner">Loading AI Trading Intelligence...</div>
      </div>
    );
  }

  return (
    <div className="ai-trading-dashboard">
      <PageHeader 
        title="AI Trading Intelligence" 
        subtitle="Monitor and manage ARIA - the AI trading assistant"
      />

      {/* Connection Status */}
      <div className="connection-info">
        <span className={`status-indicator ${isConnected ? 'connected' : 'disconnected'}`}>
          {isConnected ? '● Connected' : '○ Disconnected'}
        </span>
        <span className="status-text">
          {isConnected ? 'Real-time updates active' : 'Reconnecting...'}
        </span>
      </div>

      {error && (
        <div className="alert error">
          <span className="alert-icon">⚠️</span>
          <span className="alert-message">{error}</span>
        </div>
      )}

      {/* Tab Navigation */}
      <div className="tab-navigation">
        <button 
          className={`tab-button ${selectedTab === 'overview' ? 'active' : ''}`}
          onClick={() => setSelectedTab('overview')}
        >
          Overview
        </button>
        <button 
          className={`tab-button ${selectedTab === 'models' ? 'active' : ''}`}
          onClick={() => setSelectedTab('models')}
        >
          AI Models
        </button>
        <button 
          className={`tab-button ${selectedTab === 'predictions' ? 'active' : ''}`}
          onClick={() => setSelectedTab('predictions')}
        >
          Predictions
        </button>
        <button 
          className={`tab-button ${selectedTab === 'profiles' ? 'active' : ''}`}
          onClick={() => setSelectedTab('profiles')}
        >
          Player Profiles
        </button>
        <button 
          className={`tab-button ${selectedTab === 'market-predictions' ? 'active' : ''}`}
          onClick={() => setSelectedTab('market-predictions')}
        >
          Market Predictions
        </button>
        <button 
          className={`tab-button ${selectedTab === 'route-optimization' ? 'active' : ''}`}
          onClick={() => setSelectedTab('route-optimization')}
        >
          Route Optimization
        </button>
        <button 
          className={`tab-button ${selectedTab === 'behavior-analytics' ? 'active' : ''}`}
          onClick={() => setSelectedTab('behavior-analytics')}
        >
          Behavior Analytics
        </button>
      </div>

      {/* Tab Content */}
      <div className="tab-content">
        {selectedTab === 'overview' && (
          <div className="overview-section">
            <h2>AI Trading System Overview</h2>
            <div className="info-grid">
              <div className="info-card">
                <h3>🤖 ARIA Status</h3>
                <p>The AI Trading Assistant is operational and learning from {metrics?.activeProfiles || 0} active player profiles.</p>
                <ul>
                  <li>Price Prediction Model: {models.find(m => m.type === 'price_prediction')?.status || 'Unknown'}</li>
                  <li>Route Optimization: {models.find(m => m.type === 'route_optimization')?.status || 'Unknown'}</li>
                  <li>Behavior Analysis: {models.find(m => m.type === 'behavior_analysis')?.status || 'Unknown'}</li>
                </ul>
              </div>
              
              <div className="info-card">
                <h3>📊 Performance Metrics</h3>
                <p>Overall system accuracy: {metrics?.avgAccuracy != null ? `${metrics.avgAccuracy.toFixed(1)}%` : 'no telemetry'}</p>
                <p>{metrics?.recommendationAcceptance != null ? `Players are accepting ${metrics.recommendationAcceptance.toFixed(1)}% of AI recommendations.` : 'No recommendation telemetry exists.'}</p>
              </div>

              <div className="info-card">
                <h3>🚀 Recent Activity</h3>
                <p>Processing {metrics?.processingRate || 0} predictions per minute</p>
                <p>{metrics?.queuedJobs || 0} jobs in queue</p>
              </div>
            </div>
          </div>
        )}

        {selectedTab === 'models' && (
          <div className="models-section">
            <h2>AI Model Management</h2>
            <div className="models-grid">
              {models.map(model => (
                <div key={model.id} className="model-card">
                  <div className="model-header">
                    <h3>{model.name}</h3>
                    <span className={`model-status ${getStatusColor(model.status)}`}>
                      {model.status.toUpperCase()}
                    </span>
                  </div>
                  
                  <div className="model-stats">
                    <div className="stat-row">
                      <span>Accuracy:</span>
                      <span className="stat-value">{model.accuracy.toFixed(1)}%</span>
                    </div>
                    <div className="stat-row">
                      <span>Predictions:</span>
                      <span className="stat-value">{model.predictions.toLocaleString()}</span>
                    </div>
                    <div className="stat-row">
                      <span>Avg Response:</span>
                      <span className="stat-value">{model.avgResponseTime}ms</span>
                    </div>
                    <div className="stat-row">
                      <span>Last Trained:</span>
                      <span className="stat-value">{new Date(model.lastTrainedAt).toLocaleDateString()}</span>
                    </div>
                    <div className="stat-row">
                      <span>Next Training:</span>
                      <span className="stat-value">{new Date(model.nextTrainingAt).toLocaleDateString()}</span>
                    </div>
                  </div>

                  <div className="model-actions">
                    {model.status === 'active' && (
                      <>
                        <button 
                          className="btn btn-warning"
                          onClick={() => handleModelAction(model.id, 'stop')}
                        >
                          Stop
                        </button>
                        <button 
                          className="btn btn-primary"
                          onClick={() => handleModelAction(model.id, 'train')}
                        >
                          Retrain
                        </button>
                      </>
                    )}
                    {model.status === 'inactive' && (
                      <button 
                        className="btn btn-success"
                        onClick={() => handleModelAction(model.id, 'start')}
                      >
                        Start
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {selectedTab === 'predictions' && (
          <div className="predictions-section">
            <h2>Prediction Accuracy by Commodity</h2>
            <div className="predictions-table">
              <table>
                <thead>
                  <tr>
                    <th>Commodity</th>
                    <th>Accuracy</th>
                    <th>Total Predictions</th>
                    <th>Trend</th>
                  </tr>
                </thead>
                <tbody>
                  {predictions.map(pred => (
                    <tr key={pred.commodity}>
                      <td data-label="Commodity">
                        <span className="commodity-name">{pred.commodity}</span>
                      </td>
                      <td data-label="Accuracy">
                        <div className="accuracy-bar">
                          <div 
                            className="accuracy-fill"
                            style={{ width: `${pred.accuracy}%` }}
                          />
                          <span className="accuracy-text">{pred.accuracy.toFixed(1)}%</span>
                        </div>
                      </td>
                      <td data-label="Predictions">{pred.predictions.toLocaleString()}</td>
                      <td data-label="Trend">
                        <span className={`trend ${pred.trend}`}>
                          {pred.trend === 'improving' ? '📈' : pred.trend === 'declining' ? '📉' : '➡️'}
                          {' ' + pred.trend}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {selectedTab === 'profiles' && (
          <div className="profiles-section">
            <h2>Player AI Profiles</h2>
            <div className="profiles-table">
              <table>
                <thead>
                  <tr>
                    <th>Player</th>
                    <th>Risk Profile</th>
                    <th>AI Engagement</th>
                    <th>Profit Impact</th>
                    <th>Last Active</th>
                    <th>Trading Patterns</th>
                  </tr>
                </thead>
                <tbody>
                  {profiles.map(profile => (
                    <tr key={profile.playerId}>
                      <td data-label="Player">{profile.playerName}</td>
                      {/* Stripped from the /ai/profiles payload — see
                          PlayerProfile comment (ARIA profile scoring gap) */}
                      <td data-label="Risk Profile">—</td>
                      <td data-label="AI Engagement">—</td>
                      <td data-label="Profit Impact">—</td>
                      <td data-label="Last Active">{new Date(profile.lastActive).toLocaleDateString()}</td>
                      <td data-label="Patterns">—</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {selectedTab === 'market-predictions' && (
          <div className="market-predictions-section">
            <h2>Market Price Predictions</h2>
            <MarketPredictionInterface />
          </div>
        )}

        {selectedTab === 'route-optimization' && (
          <div className="route-optimization-section">
            <h2>AI Route Optimization</h2>
            <RouteOptimizationDisplay />
          </div>
        )}

        {selectedTab === 'behavior-analytics' && (
          <div className="behavior-analytics-section">
            <h2>Player Behavior Analytics</h2>
            <PlayerBehaviorAnalytics />
          </div>
        )}
      </div>
      {/* System Overview */}
      {metrics && (
        <div className="system-overview">
          <div className="metric-card">
            <h3>Total Predictions</h3>
            <div className="metric-value">{metrics.totalPredictions != null ? metrics.totalPredictions.toLocaleString() : '—'}</div>
            <div className="metric-label">Lifetime</div>
          </div>
          <div className="metric-card">
            <h3>Average Accuracy</h3>
            <div className="metric-value">{metrics.avgAccuracy != null ? `${metrics.avgAccuracy.toFixed(1)}%` : '—'}</div>
            <div className="metric-trend">Model Performance</div>
          </div>
          <div className="metric-card">
            <h3>Active Profiles</h3>
            <div className="metric-value">{metrics.activeProfiles.toLocaleString()}</div>
            <div className="metric-label">Players Using AI</div>
          </div>
          <div className="metric-card">
            <h3>Acceptance Rate</h3>
            <div className="metric-value">{metrics.recommendationAcceptance != null ? `${metrics.recommendationAcceptance.toFixed(1)}%` : '—'}</div>
            <div className="metric-label">Recommendations Followed</div>
          </div>
          <div className={`metric-card ${getStatusColor(metrics.modelHealth)}`}>
            <h3>System Health</h3>
            <div className="metric-value">{metrics.modelHealth != null ? metrics.modelHealth.toUpperCase() : '—'}</div>
            <div className="metric-label">
              {metrics.queuedJobs != null && metrics.processingRate != null ? `${metrics.queuedJobs} jobs queued • ${metrics.processingRate}/min` : 'no job queue exists'}
            </div>
          </div>
        </div>
      )}


    </div>
  );
};

export default AITradingDashboard;