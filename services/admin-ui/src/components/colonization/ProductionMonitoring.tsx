import React, { useState, useEffect } from 'react';
import { Line, Doughnut } from 'react-chartjs-2';
import { useAuth } from '../../contexts/AuthContext';
import './production-monitoring.css';

interface ProductionData {
  timestamp: string;
  energy: number;
  minerals: number;
  food: number;
  water: number;
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
  type: 'shortage' | 'surplus' | 'efficiency' | 'maintenance';
  severity: 'low' | 'medium' | 'high';
  resource: string;
  colony: string;
  message: string;
  timestamp: string;
}

interface ProductionStats {
  totalProduction: {
    energy: number;
    minerals: number;
    food: number;
    water: number;
  };
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

export const ProductionMonitoring: React.FC = () => {
  useAuth();
  const [timeRange, setTimeRange] = useState<'hour' | 'day' | 'week' | 'month'>('day');
  const [selectedResource, setSelectedResource] = useState<'all' | 'energy' | 'minerals' | 'food' | 'water'>('all');
  const [productionHistory, setProductionHistory] = useState<ProductionData[]>([]);
  const [trends, setTrends] = useState<ProductionTrend[]>([]);
  const [alerts, setAlerts] = useState<ProductionAlert[]>([]);
  const [stats, setStats] = useState<ProductionStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);

  useEffect(() => {
    loadProductionData();
    const interval = autoRefresh ? setInterval(loadProductionData, 10000) : null;
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [timeRange, selectedResource, autoRefresh]);

  const loadProductionData = async () => {
    try {
      const token = localStorage.getItem('accessToken');
      const response = await fetch(`/api/v1/admin/colonization/production?timeRange=${timeRange}&resource=${selectedResource}`, {
        headers: {
          'Authorization': `Bearer ${token}`,
        },
      });

      if (!response.ok) {
        throw new Error('Failed to load production data');
      }

      const data = await response.json();
      setProductionHistory(data.history);
      setTrends(data.trends);
      setAlerts(data.alerts);
      setStats(data.stats);
    } catch (err) {
      console.error('Error loading production data:', err);
      setProductionHistory([]);
      setTrends([]);
      setAlerts([]);
      setStats(null);
    } finally {
      setLoading(false);
    }
  };


  const getChartData = () => {
    const labels = productionHistory.map(data => {
      const date = new Date(data.timestamp);
      if (timeRange === 'hour') return date.toLocaleTimeString();
      if (timeRange === 'day') return date.getHours() + ':00';
      return date.toLocaleDateString();
    });

    const datasets = [];
    const colors = {
      energy: 'rgb(255, 206, 86)',
      minerals: 'rgb(54, 162, 235)',
      food: 'rgb(75, 192, 192)',
      water: 'rgb(153, 102, 255)',
    };

    if (selectedResource === 'all') {
      Object.entries(colors).forEach(([resource, color]) => {
        datasets.push({
          label: resource.charAt(0).toUpperCase() + resource.slice(1),
          data: productionHistory.map(data => data[resource as keyof typeof data]),
          borderColor: color,
          backgroundColor: color.replace('rgb', 'rgba').replace(')', ', 0.1)'),
          tension: 0.1,
        });
      });
    } else {
      datasets.push({
        label: selectedResource.charAt(0).toUpperCase() + selectedResource.slice(1),
        data: productionHistory.map(data => data[selectedResource as keyof typeof data]),
        borderColor: colors[selectedResource],
        backgroundColor: colors[selectedResource].replace('rgb', 'rgba').replace(')', ', 0.1)'),
        tension: 0.1,
      });
    }

    return { labels, datasets };
  };

  const getEfficiencyData = () => {
    const data = trends.map(t => t.efficiency);
    const labels = trends.map(t => t.resource.charAt(0).toUpperCase() + t.resource.slice(1));
    const colors = trends.map(t => {
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
      case 'shortage': return '⚠️';
      case 'surplus': return '📦';
      case 'efficiency': return '⚙️';
      case 'maintenance': return '🔧';
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

  const formatNumber = (num: number) => {
    return new Intl.NumberFormat().format(num);
  };

  if (loading) {
    return <div className="production-monitoring loading">Loading production data...</div>;
  }

  return (
    <div className="production-monitoring">
      <div className="monitoring-header">
        <h2>Production Monitoring</h2>
        <div className="header-controls">
          <select
            value={timeRange}
            onChange={(e) => setTimeRange(e.target.value as any)}
            className="time-range-select"
          >
            <option value="hour">Last Hour</option>
            <option value="day">Last 24 Hours</option>
            <option value="week">Last Week</option>
            <option value="month">Last Month</option>
          </select>
          <select
            value={selectedResource}
            onChange={(e) => setSelectedResource(e.target.value as any)}
            className="resource-select"
          >
            <option value="all">All Resources</option>
            <option value="energy">Energy</option>
            <option value="minerals">Minerals</option>
            <option value="food">Food</option>
            <option value="water">Water</option>
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
        <div className="chart-container production-chart">
          <h3>Production Over Time</h3>
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
          <h3>Resource Trends</h3>
          <div className="trends-list">
            {trends.map(trend => (
              <div key={trend.resource} className="trend-item">
                <div className="trend-header">
                  <span className="trend-resource">
                    {trend.resource.charAt(0).toUpperCase() + trend.resource.slice(1)}
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
                  <span className="efficiency-label">Efficiency: {trend.efficiency}%</span>
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
          <h3>Production Efficiency</h3>
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

        <div className="alerts-container">
          <h3>Production Alerts</h3>
          <div className="alerts-list">
            {alerts.map(alert => (
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
                <div className="alert-resource">Resource: {alert.resource}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="stats-container">
          <h3>Production Statistics</h3>
          <div className="stats-section">
            <h4>Total Production</h4>
            <div className="total-production">
              {stats && Object.entries(stats.totalProduction).map(([resource, amount]) => (
                <div key={resource} className="production-stat">
                  <span className="resource-name">
                    {resource.charAt(0).toUpperCase() + resource.slice(1)}
                  </span>
                  <span className="resource-amount">{formatNumber(amount)}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="stats-section">
            <h4>Top Producers</h4>
            <div className="top-producers">
              {stats?.topProducers.slice(0, 5).map((producer, index) => (
                <div key={index} className="producer-item">
                  <span className="producer-rank">#{index + 1}</span>
                  <span className="producer-name">{producer.colonyName}</span>
                  <span className="producer-resource">{producer.resource}</span>
                  <span className="producer-amount">{formatNumber(producer.amount)}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="stats-section">
            <h4>Production Bottlenecks</h4>
            <div className="bottlenecks">
              {stats?.bottlenecks.map((bottleneck, index) => (
                <div key={index} className="bottleneck-item">
                  <span className="bottleneck-colony">{bottleneck.colonyName}</span>
                  <span className="bottleneck-issue">{bottleneck.issue}</span>
                  <span className="bottleneck-impact">-{bottleneck.impact}%</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};