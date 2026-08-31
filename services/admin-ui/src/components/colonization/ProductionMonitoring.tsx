import React, { useState, useEffect } from 'react';
import { Line, Doughnut } from 'react-chartjs-2';
import { api } from '../../utils/auth';
import { formatAdminApiError } from '../../utils/adminApiError';
import './production-monitoring.css';

type CommodityKey = 'fuel_ore' | 'organics' | 'equipment';

interface ProductionData {
  timestamp: string;
  fuel_ore: number;
  organics: number;
  equipment: number;
}

interface ProductionTrend {
  resource: string;
  current: number;
  average: number;
  peak: number;
  trend: 'increasing' | 'decreasing' | 'stable';
  efficiency: number;
}

interface ProductionAlert {
  id: string;
  type: 'overflow' | 'starvation';
  severity: 'low' | 'medium' | 'high';
  resource: string;
  colony: string;
  message: string;
  timestamp: string;
}

interface ProductionStats {
  totalProduction: Record<CommodityKey, number>;
  topProducers: Array<{
    colonyId: string;
    colonyName: string;
    resource: string;
    amount: number;
  }>;
  bottlenecks: Array<{
    colonyId: string;
    colonyName: string;
    issue: string;
    impact: number;
  }>;
}

const COMMODITY_LABELS: Record<CommodityKey, string> = {
  fuel_ore: 'Fuel Ore',
  organics: 'Organics',
  equipment: 'Equipment',
};

const COMMODITY_COLORS: Record<CommodityKey, string> = {
  fuel_ore: 'rgb(255, 206, 86)',
  organics: 'rgb(75, 192, 192)',
  equipment: 'rgb(54, 162, 235)',
};

const formatResourceLabel = (resource: string) =>
  COMMODITY_LABELS[resource as CommodityKey] ??
  resource.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

export const ProductionMonitoring: React.FC = () => {
  const [timeRange, setTimeRange] = useState<'hour' | 'day' | 'week' | 'month'>('day');
  const [selectedResource, setSelectedResource] = useState<'all' | CommodityKey>('all');
  const [productionHistory, setProductionHistory] = useState<ProductionData[]>([]);
  const [trends, setTrends] = useState<ProductionTrend[]>([]);
  const [alerts, setAlerts] = useState<ProductionAlert[]>([]);
  const [stats, setStats] = useState<ProductionStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadProductionData();
    const interval = autoRefresh ? setInterval(loadProductionData, 10000) : null;
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [timeRange, selectedResource, autoRefresh]);

  const loadProductionData = async () => {
    try {
      const response = await api.get<{
        history?: ProductionData[];
        trends?: ProductionTrend[];
        alerts?: ProductionAlert[];
        stats?: ProductionStats | null;
      }>(`/api/v1/admin/colonization/production?timeRange=${timeRange}&resource=${selectedResource}`);

      setProductionHistory(response.data.history ?? []);
      setTrends(response.data.trends ?? []);
      setAlerts(response.data.alerts ?? []);
      setStats(response.data.stats ?? null);
      setError(null);
    } catch (err) {
      console.error('Error loading production data:', err);
      setError(
        formatAdminApiError(err, {
          fallback: 'Gameserver unreachable — network error fetching production data',
          scopeHint:
            'production monitoring requires the admin regions view scope (REGIONS_VIEW).',
          notFoundMessage:
            'Production monitoring route not found (404). The gameserver ships /api/v1/admin/colonization/production — ' +
            'check that the gameserver is running and the /api proxy is reaching it.',
        })
      );
      setProductionHistory([]);
      setTrends([]);
      setAlerts([]);
      setStats(null);
    } finally {
      setLoading(false);
    }
  };

  const getChartData = () => {
    const labels = productionHistory.map((data) => {
      const date = new Date(data.timestamp);
      if (timeRange === 'hour') return date.toLocaleTimeString();
      if (timeRange === 'day') return date.toLocaleString();
      return date.toLocaleDateString();
    });

    const datasets: Array<{
      label: string;
      data: number[];
      borderColor: string;
      backgroundColor: string;
      tension: number;
    }> = [];
    const commodities: CommodityKey[] =
      selectedResource === 'all'
        ? ['fuel_ore', 'organics', 'equipment']
        : [selectedResource];

    commodities.forEach((commodity) => {
      datasets.push({
        label: COMMODITY_LABELS[commodity],
        data: productionHistory.map((data) => data[commodity]),
        borderColor: COMMODITY_COLORS[commodity],
        backgroundColor: COMMODITY_COLORS[commodity].replace('rgb', 'rgba').replace(')', ', 0.1)'),
        tension: 0.1,
      });
    });

    return { labels, datasets };
  };

  const getEfficiencyData = () => {
    const data = trends.map((t) => t.efficiency);
    const labels = trends.map((t) => formatResourceLabel(t.resource));
    const colors = trends.map((t) => {
      if (t.efficiency >= 90) return 'rgb(75, 192, 192)';
      if (t.efficiency >= 70) return 'rgb(255, 206, 86)';
      return 'rgb(255, 99, 132)';
    });

    return {
      labels,
      datasets: [{
        data,
        backgroundColor: colors,
        borderWidth: 0,
      }],
    };
  };

  const getTrendIcon = (trend: string) => {
    switch (trend) {
      case 'increasing': return '📈';
      case 'decreasing': return '📉';
      case 'stable': return '➡️';
      default: return '❓';
    }
  };

  const getAlertIcon = (type: ProductionAlert['type']) => {
    switch (type) {
      case 'overflow': return '📦';
      case 'starvation': return '⚠️';
      default: return '❓';
    }
  };

  const getSeverityColor = (severity: ProductionAlert['severity']) => {
    switch (severity) {
      case 'high': return 'var(--error-color)';
      case 'medium': return 'var(--warning-color)';
      case 'low': return 'var(--info-color)';
      default: return 'var(--text-secondary)';
    }
  };

  const formatNumber = (num: number) => new Intl.NumberFormat().format(num);

  if (loading) {
    return <div className="production-monitoring loading">Loading production data...</div>;
  }

  if (error) {
    return (
      <div className="production-monitoring">
        <div className="monitoring-header">
          <h2>Production Monitoring</h2>
        </div>
        <div
          role="alert"
          style={{
            margin: '0 0 16px 0',
            padding: '10px 12px',
            background: 'rgba(239, 68, 68, 0.12)',
            border: '1px solid rgba(239, 68, 68, 0.35)',
            borderRadius: '6px',
            color: '#fca5a5',
            fontSize: '0.85rem',
            lineHeight: 1.4,
          }}
        >
          {error}
        </div>
        <button type="button" className="refresh-button" onClick={() => { setLoading(true); loadProductionData(); }}>
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="production-monitoring">
      <div className="monitoring-header">
        <h2>Production Monitoring</h2>
        <div className="header-controls">
          <select
            value={timeRange}
            onChange={(e) => setTimeRange(e.target.value as typeof timeRange)}
            className="time-range-select"
          >
            <option value="hour">Last Hour</option>
            <option value="day">Last 24 Hours</option>
            <option value="week">Last Week</option>
            <option value="month">Last Month</option>
          </select>
          <select
            value={selectedResource}
            onChange={(e) => setSelectedResource(e.target.value as typeof selectedResource)}
            className="resource-select"
          >
            <option value="all">All Commodities</option>
            <option value="fuel_ore">Fuel Ore</option>
            <option value="organics">Organics</option>
            <option value="equipment">Equipment</option>
          </select>
          <button
            className={`refresh-button ${autoRefresh ? 'active' : ''}`}
            onClick={() => setAutoRefresh(!autoRefresh)}
          >
            {autoRefresh ? '🔄 Auto' : '⏸️ Paused'}
          </button>
        </div>
      </div>

      <div className="monitoring-grid">
        <div className="alerts-container">
          <h3>Tick Warnings</h3>
          <div className="alerts-list">
            {alerts.length === 0 ? (
              <p className="alerts-empty" data-testid="production-alerts-empty">
                No overflow or starvation warnings — all colonies healthy at last tick.
              </p>
            ) : (
              alerts.map((alert) => (
                <div
                  key={alert.id}
                  className={`alert-item ${alert.severity}`}
                  style={{ borderLeftColor: getSeverityColor(alert.severity) }}
                >
                  <div className="alert-header">
                    <span className="alert-icon">{getAlertIcon(alert.type)}</span>
                    <span className="alert-colony">{alert.colony}</span>
                    <span className="alert-time">
                      {new Date(alert.timestamp).toLocaleTimeString()}
                    </span>
                  </div>
                  <div className="alert-message">{alert.message}</div>
                  <div className="alert-resource">Resource: {formatResourceLabel(alert.resource)}</div>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="chart-container production-chart">
          <h3>Commodity Stockpiles</h3>
          <div style={{ position: 'relative', height: '300px', width: '100%' }}>
            <Line
              data={getChartData()}
              options={{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                  legend: {
                    display: true,
                    position: 'top',
                  },
                },
                scales: {
                  y: {
                    beginAtZero: true,
                  },
                },
              }}
            />
          </div>
        </div>

        <div className="trends-container">
          <h3>Commodity Totals</h3>
          <div className="trends-list">
            {trends.map((trend) => (
              <div key={trend.resource} className="trend-item">
                <div className="trend-header">
                  <span className="trend-resource">
                    {formatResourceLabel(trend.resource)}
                  </span>
                  <span className="trend-icon">{getTrendIcon(trend.trend)}</span>
                </div>
                <div className="trend-stats">
                  <div className="trend-stat">
                    <span className="stat-label">Current</span>
                    <span className="stat-value">{formatNumber(trend.current)}</span>
                  </div>
                  <div className="trend-stat">
                    <span className="stat-label">Average</span>
                    <span className="stat-value">{formatNumber(trend.average)}</span>
                  </div>
                  <div className="trend-stat">
                    <span className="stat-label">Peak</span>
                    <span className="stat-value">{formatNumber(trend.peak)}</span>
                  </div>
                </div>
                <div className="efficiency-bar">
                  <span className="efficiency-label">Within cap: {trend.efficiency}%</span>
                  <div className="bar-background">
                    <div
                      className="bar-fill"
                      style={{
                        width: `${trend.efficiency}%`,
                        backgroundColor: trend.efficiency >= 90 ? 'var(--success-color)' :
                          trend.efficiency >= 70 ? 'var(--warning-color)' : 'var(--error-color)'
                      }}
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="chart-container efficiency-chart">
          <h3>Cap Utilization</h3>
          <div style={{ position: 'relative', height: '250px', width: '100%' }}>
            <Doughnut
              data={getEfficiencyData()}
              options={{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                  legend: {
                    position: 'bottom',
                  },
                },
              }}
            />
          </div>
        </div>

        <div className="stats-container">
          <h3>Stockpile Statistics</h3>
          <div className="stats-section">
            <h4>Empire Totals</h4>
            <div className="total-production">
              {stats && Object.entries(stats.totalProduction).map(([resource, amount]) => (
                <div key={resource} className="production-stat">
                  <span className="resource-name">
                    {formatResourceLabel(resource)}
                  </span>
                  <span className="resource-amount">{formatNumber(amount)}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="stats-section">
            <h4>Top Stockpiles</h4>
            <div className="top-producers">
              {stats?.topProducers.slice(0, 5).map((producer, index) => (
                <div key={index} className="producer-item">
                  <span className="producer-rank">#{index + 1}</span>
                  <span className="producer-name">{producer.colonyName}</span>
                  <span className="producer-resource">{formatResourceLabel(producer.resource)}</span>
                  <span className="producer-amount">{formatNumber(producer.amount)}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="stats-section">
            <h4>Production Bottlenecks</h4>
            <div className="bottlenecks">
              {stats?.bottlenecks.length ? (
                stats.bottlenecks.map((bottleneck, index) => (
                  <div key={index} className="bottleneck-item">
                    <span className="bottleneck-colony">{bottleneck.colonyName}</span>
                    <span className="bottleneck-issue">{bottleneck.issue}</span>
                    <span className="bottleneck-impact">-{bottleneck.impact}%</span>
                  </div>
                ))
              ) : (
                <p className="bottlenecks-empty" data-testid="production-bottlenecks-empty">
                  No active bottlenecks.
                </p>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
