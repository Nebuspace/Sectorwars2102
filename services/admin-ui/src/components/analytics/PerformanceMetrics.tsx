import React, { useState, useEffect } from 'react';
import { Line, Doughnut } from 'react-chartjs-2';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  ArcElement,
  Title,
  Tooltip,
  Legend
} from 'chart.js';
import { api } from '../../utils/auth';
import './performance-metrics.css';

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  ArcElement,
  Title,
  Tooltip,
  Legend
);

interface SystemMetrics {
  serverLoad: number;
  memoryUsage: number;
  diskUsage: number;
  networkLatency: number;
  activeConnections: number;
  requestsPerSecond: number;
  errorRate: number;
  uptime: number;
}

interface DatabaseMetrics {
  queryTime: number;
  activeQueries: number;
  slowQueries: number;
  connectionPool: {
    active: number;
    idle: number;
    total: number;
  };
  cacheHitRate: number;
}

interface ApplicationMetrics {
  responseTime: {
    p50: number;
    p95: number;
    p99: number;
  };
  throughput: number;
  errorCount: number;
  successRate: number;
  endpoints: Array<{
    path: string;
    avgTime: number;
    calls: number;
    errors: number;
  }>;
}

interface OptimizationSuggestion {
  id: string;
  title: string;
  description: string;
  impact: 'high' | 'medium' | 'low';
  effort: 'high' | 'medium' | 'low';
  category: string;
  estimatedImprovement: string;
}

// GET /api/v1/admin/performance/metrics ships, but several of its fields are
// hardcoded placeholders rather than measurements — the gameserver has no
// psutil, no pg_stat_statements, and no in-band request/error instrumentation
// (sw2102-docs OPERATIONS/admin-ui.md § GET /performance/metrics). Those fields
// arrive as literal 0 and must never be rendered as a measured value: a 0 that
// means "never measured" and a 0 that means "genuinely none right now" look
// identical in the payload, so availability is decided per FIELD here, never by
// testing whether a value happens to be zero.
const NO_PSUTIL = 'psutil not installed on the gameserver';
const NO_PG_STAT_STATEMENTS = 'requires the pg_stat_statements extension';
const NO_REQUEST_TIMING = 'no in-band request timing';
const NO_ERROR_TRACKING = 'no in-band error tracking';

const UnavailableCard: React.FC<{ icon: string; label: string; reason: string }> = ({
  icon,
  label,
  reason,
}) => (
  <div className="metric-card unavailable">
    <div className="metric-icon">
      <i className={`fas ${icon}`}></i>
    </div>
    <div className="metric-content">
      <span className="metric-label">{label}</span>
      <span className="metric-value unavailable">n/a</span>
      <span className="metric-note">{reason}</span>
    </div>
  </div>
);

const UnavailableStat: React.FC<{ label: string; reason: string }> = ({ label, reason }) => (
  <div className="stat-item">
    <span className="stat-label">{label}</span>
    <span className="stat-value unavailable">n/a</span>
    <span className="metric-note">{reason}</span>
  </div>
);

// The endpoint reports Postgres uptime as the postmaster's age expressed as a
// share of a 30-day window (capped at 100), NOT as an availability percentage.
// Recover the underlying age so the card can say what it actually measured.
// At the cap the true age is unknowable from the payload — anything at or past
// 30 days arrives as exactly 100 — so report the bound, not a precise "30d 0h".
const formatPostgresAge = (windowPct: number) => {
  if (windowPct >= 100) return '≥30d';

  const totalMinutes = Math.max(0, Math.round((windowPct / 100) * 30 * 24 * 60));
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;

  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
};

const responseStatus = (err: unknown): number | undefined =>
  typeof err === 'object' && err !== null && 'response' in err
    ? (err as { response?: { status?: number } }).response?.status
    : undefined;

export const PerformanceMetrics: React.FC = () => {
  const [systemMetrics, setSystemMetrics] = useState<SystemMetrics | null>(null);
  const [databaseMetrics, setDatabaseMetrics] = useState<DatabaseMetrics | null>(null);
  const [applicationMetrics, setApplicationMetrics] = useState<ApplicationMetrics | null>(null);
  const [historicalData, setHistoricalData] = useState<any>(null);
  const [suggestions, setSuggestions] = useState<OptimizationSuggestion[]>([]);
  const [selectedTimeRange, setSelectedTimeRange] = useState<'1h' | '6h' | '24h' | '7d'>('24h');
  const [loading, setLoading] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Initial fetch + refetch on time-range change only. Error state is NOT a
  // dependency here, so a failure never triggers an automatic refetch loop —
  // recovery is via the manual Retry button.
  useEffect(() => {
    fetchPerformanceData();
  }, [selectedTimeRange]);

  // Polling interval — paused while in an error state so we don't hammer a
  // failing endpoint. No immediate fetch in this effect, so toggling error
  // only starts/stops the timer.
  useEffect(() => {
    if (!autoRefresh || error) return;
    const interval = setInterval(fetchPerformanceData, 5000); // Refresh every 5 seconds
    return () => clearInterval(interval);
  }, [selectedTimeRange, autoRefresh, error]);

  const fetchPerformanceData = async () => {
    try {
      const { data } = await api.get('/api/v1/admin/performance/metrics', {
        params: { timeRange: selectedTimeRange },
      });

      setSystemMetrics(data.system);
      setDatabaseMetrics(data.database);
      setApplicationMetrics(data.application);
      setHistoricalData(data.historical);
      setSuggestions(data.suggestions ?? []);
      setError(null);
    } catch (err) {
      console.error('Error fetching performance data:', err);
      const status = responseStatus(err);

      if (status === 401 || status === 403) {
        setError(
          'Access denied — reading performance metrics requires the admin.audit.view scope.'
        );
      } else if (status === 404) {
        setError(
          'Performance metrics route not found (404). The endpoint ships in the gameserver — ' +
            'check that the gameserver is running and the /api proxy is reaching it.'
        );
      } else if (status !== undefined) {
        setError(`Performance metrics request failed (HTTP ${status})`);
      } else {
        setError('Gameserver unreachable — network error fetching performance metrics');
      }
    } finally {
      setLoading(false);
    }
  };

  // Only the transaction-volume series carries real data; the load and error
  // series arrive zero-padded and are reported as unavailable below rather than
  // drawn as flat lines that look like a measured steady state.
  const historicalChartData = historicalData ? {
    labels: historicalData.timestamps,
    datasets: [
      {
        label: 'Market transactions per bucket',
        data: historicalData.responseTime,
        borderColor: '#10b981',
        backgroundColor: 'rgba(16, 185, 129, 0.1)',
        tension: 0.3
      }
    ]
  } : null;

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: {
      mode: 'index' as const,
      intersect: false,
    },
    plugins: {
      legend: {
        position: 'top' as const,
        labels: {
          color: '#94a3b8'
        }
      }
    },
    scales: {
      x: {
        grid: {
          color: 'rgba(148, 163, 184, 0.1)'
        },
        ticks: {
          color: '#94a3b8'
        }
      },
      y: {
        type: 'linear' as const,
        display: true,
        position: 'left' as const,
        beginAtZero: true,
        grid: {
          color: 'rgba(148, 163, 184, 0.1)'
        },
        ticks: {
          color: '#94a3b8'
        },
        title: {
          display: true,
          text: 'Transactions',
          color: '#94a3b8'
        }
      }
    }
  };

  // `total` counts every non-null pg_stat_activity state, while active/idle
  // cover only a subset — the remainder is other states, not spare capacity.
  const doughnutData = databaseMetrics ? {
    labels: ['Active', 'Idle', 'Other states'],
    datasets: [{
      data: [
        databaseMetrics.connectionPool.active,
        databaseMetrics.connectionPool.idle,
        Math.max(
          0,
          databaseMetrics.connectionPool.total -
            databaseMetrics.connectionPool.active -
            databaseMetrics.connectionPool.idle
        )
      ],
      backgroundColor: ['#3b82f6', '#fbbf24', '#10b981'],
      borderWidth: 0
    }]
  } : null;

  const doughnutOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        position: 'bottom' as const,
        labels: {
          color: '#94a3b8',
          padding: 10
        }
      }
    }
  };

  if (loading && !systemMetrics) {
    return (
      <div className="performance-loading">
        <i className="fas fa-spinner fa-spin"></i>
        <span>Loading performance metrics...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="performance-metrics">
        <div className="metrics-header">
          <h2>Performance Optimization Metrics</h2>
        </div>
        <div className="alert alert-error" role="alert">
          <span className="alert-icon">⚠️</span>
          <span className="alert-message">{error}</span>
        </div>
        <button
          className="btn btn-secondary"
          onClick={() => {
            setLoading(true);
            fetchPerformanceData();
          }}
        >
          <i className="fas fa-sync"></i>
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="performance-metrics">
      <div className="metrics-header">
        <h2>Performance Optimization Metrics</h2>
        <div className="header-controls">
          <label className="auto-refresh">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            Auto-refresh
          </label>
          <div className="time-selector">
            <button
              className={`time-btn ${selectedTimeRange === '1h' ? 'active' : ''}`}
              onClick={() => setSelectedTimeRange('1h')}
            >
              1 Hour
            </button>
            <button
              className={`time-btn ${selectedTimeRange === '6h' ? 'active' : ''}`}
              onClick={() => setSelectedTimeRange('6h')}
            >
              6 Hours
            </button>
            <button
              className={`time-btn ${selectedTimeRange === '24h' ? 'active' : ''}`}
              onClick={() => setSelectedTimeRange('24h')}
            >
              24 Hours
            </button>
            <button
              className={`time-btn ${selectedTimeRange === '7d' ? 'active' : ''}`}
              onClick={() => setSelectedTimeRange('7d')}
            >
              7 Days
            </button>
          </div>
        </div>
      </div>

      {systemMetrics && (
        <div className="metrics-grid">
          <div className="metric-section system-metrics">
            <h3>System Metrics</h3>
            <div className="metric-cards">
              <div className="metric-card">
                <div className="metric-icon">
                  <i className="fas fa-plug"></i>
                </div>
                <div className="metric-content">
                  <span className="metric-label">Active DB Connections</span>
                  <span className="metric-value">{systemMetrics.activeConnections}</span>
                  <span className="metric-note">live pg_stat_activity count</span>
                </div>
              </div>
              <div className="metric-card">
                <div className="metric-icon">
                  <i className="fas fa-exchange-alt"></i>
                </div>
                <div className="metric-content">
                  <span className="metric-label">Market Trades / sec</span>
                  <span className="metric-value">{systemMetrics.requestsPerSecond.toFixed(3)}</span>
                  <span className="metric-note">last minute — a trade-rate proxy, not HTTP request rate</span>
                </div>
              </div>
              <div className="metric-card">
                <div className="metric-icon">
                  <i className="fas fa-clock"></i>
                </div>
                <div className="metric-content">
                  <span className="metric-label">Postgres Process Age</span>
                  <span className="metric-value">{formatPostgresAge(systemMetrics.uptime)}</span>
                  <span className="metric-note">
                    {systemMetrics.uptime >= 100
                      ? 'at or past the endpoint\u2019s 30-day cap — process age, not an availability SLA'
                      : `${systemMetrics.uptime.toFixed(2)}% of a 30-day window — process age, not an availability SLA`}
                  </span>
                </div>
              </div>
              <UnavailableCard icon="fa-server" label="Server Load" reason={NO_PSUTIL} />
              <UnavailableCard icon="fa-memory" label="Memory Usage" reason={NO_PSUTIL} />
              <UnavailableCard icon="fa-hdd" label="Disk Usage" reason={NO_PSUTIL} />
              <UnavailableCard icon="fa-network-wired" label="Network Latency" reason={NO_PSUTIL} />
              <UnavailableCard icon="fa-bug" label="Error Rate" reason={NO_ERROR_TRACKING} />
            </div>
          </div>

          {databaseMetrics && (
            <div className="metric-section database-metrics">
              <h3>Database Performance</h3>
              <div className="database-grid">
                <div className="db-stats">
                  <UnavailableStat label="Average Query Time" reason={NO_PG_STAT_STATEMENTS} />
                  <div className="stat-item">
                    <span className="stat-label">Active Queries</span>
                    <span className="stat-value">{databaseMetrics.activeQueries}</span>
                  </div>
                  <div className="stat-item">
                    <span className="stat-label">Slow Queries (&gt;1s)</span>
                    <span className={`stat-value ${databaseMetrics.slowQueries > 0 ? 'warning' : ''}`}>
                      {databaseMetrics.slowQueries}
                    </span>
                  </div>
                  <div className="stat-item">
                    <span className="stat-label">Cache Hit Rate</span>
                    <span className={`stat-value ${databaseMetrics.cacheHitRate >= 95 ? 'good' : ''}`}>
                      {databaseMetrics.cacheHitRate.toFixed(1)}%
                    </span>
                  </div>
                </div>
                <div className="connection-pool">
                  <h4>Connection Pool</h4>
                  {doughnutData && (
                    <div className="pool-chart">
                      <Doughnut data={doughnutData} options={doughnutOptions} />
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {applicationMetrics && (
            <div className="metric-section application-metrics">
              <h3>Application Performance</h3>
              <div className="app-stats">
                <div className="response-times">
                  <h4>Response Time Percentiles</h4>
                  <p className="unavailable-note">
                    P50 / P95 / P99 are <strong>n/a</strong> — {NO_REQUEST_TIMING}. The endpoint
                    returns zeros for these, so no percentiles are shown.
                  </p>
                </div>
                <div className="app-metrics-summary">
                  <div className="summary-item">
                    <i className="fas fa-tachometer-alt"></i>
                    <span className="summary-label">Trade Throughput</span>
                    <span className="summary-value">{applicationMetrics.throughput} /s</span>
                    <span className="metric-note">market transactions over the selected window</span>
                  </div>
                  <div className="summary-item">
                    <i className="fas fa-check-circle"></i>
                    <span className="summary-label">Success Rate</span>
                    <span className="summary-value unavailable">n/a</span>
                    <span className="metric-note">{NO_ERROR_TRACKING}</span>
                  </div>
                  <div className="summary-item">
                    <i className="fas fa-exclamation-triangle"></i>
                    <span className="summary-label">Error Count</span>
                    <span className="summary-value unavailable">n/a</span>
                    <span className="metric-note">{NO_ERROR_TRACKING}</span>
                  </div>
                </div>
              </div>
              {applicationMetrics.endpoints?.length > 0 && (
                <div className="endpoint-performance">
                  <h4>Top Commodities by Trade Volume</h4>
                  <p className="unavailable-note">
                    Derived from market transactions, not route telemetry — per-path timings and
                    error counts are n/a ({NO_REQUEST_TIMING}).
                  </p>
                  <div className="endpoint-list">
                    {applicationMetrics.endpoints.map(endpoint => (
                      <div key={endpoint.path} className="endpoint-item">
                        <div className="endpoint-info">
                          <span className="endpoint-path">{endpoint.path}</span>
                          <div className="endpoint-stats">
                            <span>{endpoint.calls.toLocaleString()} trades</span>
                          </div>
                        </div>
                        <div className="endpoint-bar">
                          <div
                            className="endpoint-fill"
                            style={{ width: `${Math.min(100, (endpoint.calls / 10000) * 100)}%` }}
                          ></div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {historicalChartData && (
        <div className="historical-trends">
          <h3>Trade Volume Trend</h3>
          <p className="unavailable-note">
            Transaction volume per bucket over the selected window. Server-load and error-rate
            history are n/a — the endpoint zero-pads those series ({NO_PSUTIL}; {NO_ERROR_TRACKING}).
          </p>
          <div className="trends-chart">
            <Line data={historicalChartData} options={chartOptions} />
          </div>
        </div>
      )}

      {suggestions.length > 0 && (
      <div className="optimization-suggestions">
        <h3>Optimization Suggestions</h3>
        <div className="suggestions-grid">
          {suggestions.map(suggestion => (
            <div key={suggestion.id} className={`suggestion-card impact-${suggestion.impact} effort-${suggestion.effort}`}>
              <div className="suggestion-header">
                <h4>{suggestion.title}</h4>
                <div className="suggestion-badges">
                  <span className={`impact-badge ${suggestion.impact}`}>
                    {suggestion.impact} impact
                  </span>
                  <span className={`effort-badge ${suggestion.effort}`}>
                    {suggestion.effort} effort
                  </span>
                </div>
              </div>
              <p className="suggestion-description">{suggestion.description}</p>
              <div className="suggestion-footer">
                <span className="suggestion-category">
                  <i className="fas fa-tag"></i>
                  {suggestion.category}
                </span>
                <span className="suggestion-improvement">
                  <i className="fas fa-chart-line"></i>
                  {suggestion.estimatedImprovement}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>
      )}
    </div>
  );
};
