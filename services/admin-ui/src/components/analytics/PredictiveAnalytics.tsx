import React, { useState, useEffect } from 'react';
import { Line, Bar } from 'react-chartjs-2';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Title,
  Tooltip,
  Legend,
  Filler
} from 'chart.js';
import './predictive-analytics.css';

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Title,
  Tooltip,
  Legend,
  Filler
);

interface Prediction {
  metric: string;
  current: number;
  predicted: number;
  confidence: number;
  trend: 'up' | 'down' | 'stable';
  change: number;
  factors: string[];
}

interface TimeSeriesData {
  labels: string[];
  actual: number[];
  predicted: number[];
  upperBound: number[];
  lowerBound: number[];
}

interface RiskFactor {
  id: string;
  name: string;
  severity: 'low' | 'medium' | 'high' | 'critical';
  probability: number;
  impact: string;
  mitigation: string;
}

export const PredictiveAnalytics: React.FC = () => {
  const [predictions, setPredictions] = useState<Prediction[]>([]);
  const [playerGrowth, setPlayerGrowth] = useState<TimeSeriesData | null>(null);
  const [economyForecast, setEconomyForecast] = useState<TimeSeriesData | null>(null);
  const [riskFactors, setRiskFactors] = useState<RiskFactor[]>([]);
  const [selectedTimeframe, setSelectedTimeframe] = useState<'7d' | '30d' | '90d'>('30d');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchPredictiveData();
  }, [selectedTimeframe]);

  const fetchPredictiveData = async () => {
    setLoading(true);
    try {
      const response = await fetch(`/api/v1/admin/analytics/predictions?timeframe=${selectedTimeframe}`, {
        headers: { 'Authorization': `Bearer ${localStorage.getItem('accessToken')}` }
      });

      if (!response.ok) {
        setError(
          response.status === 404
            ? 'Predictive analytics endpoint not implemented — /api/v1/admin/analytics/predictions returned 404'
            : `Predictive analytics request failed (HTTP ${response.status})`
        );
        return;
      }

      const data = await response.json();
      setPredictions(data.predictions ?? []);
      setPlayerGrowth(data.playerGrowth);
      setEconomyForecast(data.economyForecast);
      setRiskFactors(data.riskFactors ?? []);
      setError(null);
    } catch (err) {
      console.error('Error fetching predictive data:', err);
      setError('Gameserver unreachable — network error fetching predictive analytics');
    } finally {
      setLoading(false);
    }
  };

  const getTrendIcon = (trend: string) => {
    switch (trend) {
      case 'up':
        return <i className="fas fa-arrow-up trend-up"></i>;
      case 'down':
        return <i className="fas fa-arrow-down trend-down"></i>;
      default:
        return <i className="fas fa-minus trend-stable"></i>;
    }
  };

  const getSeverityClass = (severity: string) => {
    return `severity-${severity}`;
  };

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        position: 'top' as const,
        labels: {
          color: '#94a3b8'
        }
      },
      title: {
        display: false
      },
      tooltip: {
        mode: 'index' as const,
        intersect: false,
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
        grid: {
          color: 'rgba(148, 163, 184, 0.1)'
        },
        ticks: {
          color: '#94a3b8'
        }
      }
    }
  };

  const playerGrowthData = playerGrowth ? {
    labels: playerGrowth.labels,
    datasets: [
      {
        label: 'Actual',
        data: playerGrowth.actual,
        borderColor: '#3b82f6',
        backgroundColor: '#3b82f6',
        fill: false,
        tension: 0.1
      },
      {
        label: 'Predicted',
        data: playerGrowth.predicted,
        borderColor: '#10b981',
        backgroundColor: '#10b981',
        borderDash: [5, 5],
        fill: false,
        tension: 0.1
      },
      {
        label: 'Upper Bound',
        data: playerGrowth.upperBound,
        borderColor: 'rgba(16, 185, 129, 0.3)',
        backgroundColor: 'rgba(16, 185, 129, 0.1)',
        fill: '+1',
        borderWidth: 1,
        pointRadius: 0,
        tension: 0.1
      },
      {
        label: 'Lower Bound',
        data: playerGrowth.lowerBound,
        borderColor: 'rgba(16, 185, 129, 0.3)',
        backgroundColor: 'rgba(16, 185, 129, 0.1)',
        fill: '-1',
        borderWidth: 1,
        pointRadius: 0,
        tension: 0.1
      }
    ]
  } : null;

  const economyData = economyForecast ? {
    labels: economyForecast.labels,
    datasets: [
      {
        label: 'Actual Revenue',
        data: economyForecast.actual,
        borderColor: '#f59e0b',
        backgroundColor: '#f59e0b',
        fill: false,
        tension: 0.1
      },
      {
        label: 'Predicted Revenue',
        data: economyForecast.predicted,
        borderColor: '#8b5cf6',
        backgroundColor: '#8b5cf6',
        borderDash: [5, 5],
        fill: false,
        tension: 0.1
      }
    ]
  } : null;

  const riskProbabilityData = {
    labels: riskFactors.map(r => r.name),
    datasets: [{
      label: 'Risk Probability %',
      data: riskFactors.map(r => r.probability),
      backgroundColor: riskFactors.map(r => {
        switch (r.severity) {
          case 'critical': return '#ef4444';
          case 'high': return '#f59e0b';
          case 'medium': return '#3b82f6';
          default: return '#10b981';
        }
      })
    }]
  };

  if (loading) {
    return (
      <div className="predictive-loading">
        <i className="fas fa-spinner fa-spin"></i>
        <span>Loading predictive analytics...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="predictive-analytics">
        <div className="analytics-header">
          <h2>Predictive Analytics Dashboard</h2>
        </div>
        <div className="alert alert-error">
          <span className="alert-icon">⚠️</span>
          <span className="alert-message">{error}</span>
        </div>
        <button
          className="btn btn-secondary"
          onClick={() => fetchPredictiveData()}
        >
          <i className="fas fa-sync"></i>
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="predictive-analytics">
      <div className="analytics-header">
        <h2>Predictive Analytics Dashboard</h2>
        <div className="timeframe-selector">
          <button
            className={`timeframe-btn ${selectedTimeframe === '7d' ? 'active' : ''}`}
            onClick={() => setSelectedTimeframe('7d')}
          >
            7 Days
          </button>
          <button
            className={`timeframe-btn ${selectedTimeframe === '30d' ? 'active' : ''}`}
            onClick={() => setSelectedTimeframe('30d')}
          >
            30 Days
          </button>
          <button
            className={`timeframe-btn ${selectedTimeframe === '90d' ? 'active' : ''}`}
            onClick={() => setSelectedTimeframe('90d')}
          >
            90 Days
          </button>
        </div>
      </div>

      <div className="predictions-grid">
        {predictions.map(prediction => (
          <div key={prediction.metric} className="prediction-card">
            <div className="prediction-header">
              <h3>{prediction.metric}</h3>
              <div className={`confidence-badge ${prediction.confidence >= 80 ? 'high' : prediction.confidence >= 60 ? 'medium' : 'low'}`}>
                {prediction.confidence}% confidence
              </div>
            </div>
            <div className="prediction-values">
              <div className="current-value">
                <span className="label">Current</span>
                <span className="value">{prediction.current.toLocaleString()}</span>
              </div>
              <div className="arrow">
                {getTrendIcon(prediction.trend)}
              </div>
              <div className="predicted-value">
                <span className="label">Predicted</span>
                <span className="value">{prediction.predicted.toLocaleString()}</span>
              </div>
            </div>
            <div className={`change-indicator ${prediction.change > 0 ? 'positive' : 'negative'}`}>
              {prediction.change > 0 ? '+' : ''}{prediction.change}%
            </div>
            <div className="prediction-factors">
              <h4>Contributing Factors:</h4>
              <ul>
                {prediction.factors.map((factor, index) => (
                  <li key={index}>{factor}</li>
                ))}
              </ul>
            </div>
          </div>
        ))}
      </div>

      <div className="charts-section">
        <div className="chart-container">
          <h3>Player Growth Forecast</h3>
          {playerGrowthData && (
            <div className="chart-wrapper">
              <Line data={playerGrowthData} options={chartOptions} />
            </div>
          )}
        </div>

        <div className="chart-container">
          <h3>Revenue Forecast</h3>
          {economyData && (
            <div className="chart-wrapper">
              <Line data={economyData} options={chartOptions} />
            </div>
          )}
        </div>
      </div>

      <div className="risk-analysis">
        <h3>Risk Factor Analysis</h3>
        <div className="risk-grid">
          <div className="risk-factors">
            {riskFactors.map(risk => (
              <div key={risk.id} className={`risk-card ${getSeverityClass(risk.severity)}`}>
                <div className="risk-header">
                  <h4>{risk.name}</h4>
                  <span className="severity-badge">{risk.severity}</span>
                </div>
                <div className="risk-probability">
                  <div className="probability-bar">
                    <div 
                      className="probability-fill"
                      style={{ width: `${risk.probability}%` }}
                    ></div>
                  </div>
                  <span className="probability-text">{risk.probability}% probability</span>
                </div>
                <div className="risk-details">
                  <div className="impact">
                    <strong>Impact:</strong> {risk.impact}
                  </div>
                  <div className="mitigation">
                    <strong>Mitigation:</strong> {risk.mitigation}
                  </div>
                </div>
              </div>
            ))}
          </div>
          <div className="risk-chart">
            <h4>Risk Probability Distribution</h4>
            <div className="chart-wrapper">
              <Bar 
                data={riskProbabilityData} 
                options={{
                  ...chartOptions,
                  indexAxis: 'y',
                  plugins: {
                    ...chartOptions.plugins,
                    legend: {
                      display: false
                    }
                  }
                }} 
              />
            </div>
          </div>
        </div>
      </div>

    </div>
  );
};