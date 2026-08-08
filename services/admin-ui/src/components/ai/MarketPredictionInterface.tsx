import React, { useState, useEffect, useCallback } from 'react';
import { useAIUpdates } from '../../contexts/WebSocketContext';
import { api } from '../../utils/auth';
import './market-prediction-interface.css';

interface PricePrediction {
  id: string;
  resourceId: string;
  resourceName: string;
  sectorId: string;
  sectorName: string;
  currentPrice: number;
  predictedPrice: number;
  confidence: number;
  timeframe: string;
  factors: string[];
  timestamp: string;
}

interface PredictionAccuracy {
  resourceId: string;
  resourceName: string;
  predictions: number;
  accurate: number;
  accuracy: number;
  avgDeviation: number;
}

export const MarketPredictionInterface: React.FC = () => {
  const [predictions, setPredictions] = useState<PricePrediction[]>([]);
  const [accuracyStats, setAccuracyStats] = useState<PredictionAccuracy[]>([]);
  const [selectedTimeframe, setSelectedTimeframe] = useState<string>('1h');
  const [selectedResource, setSelectedResource] = useState<string>('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const handlePredictionUpdate = useCallback((data: any) => {
    setPredictions(prev => {
      const updated = [...prev];
      const index = updated.findIndex(p => p.id === data.id);
      if (index >= 0) {
        updated[index] = data;
      } else {
        updated.unshift(data);
        if (updated.length > 100) updated.pop();
      }
      return updated;
    });
  }, []);

  const handleAccuracyUpdate = useCallback((data: any) => {
    setAccuracyStats(data);
  }, []);

  useAIUpdates(undefined, handlePredictionUpdate, undefined, undefined, undefined, handleAccuracyUpdate);

  useEffect(() => {
    fetchPredictions();
    fetchAccuracyStats();
  }, [selectedTimeframe, selectedResource]);

  const fetchPredictions = async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams({
        timeframe: selectedTimeframe,
        ...(selectedResource !== 'all' && { resource: selectedResource })
      });
      
      const response = await api.get(`/api/v1/admin/ai/predictions?${params}`);
      setPredictions(response.data);
    } catch (err: any) {
      const errorMessage = err.response?.data?.detail || err.message || 'Failed to load predictions';
      if (err.response?.status === 401) {
        setError('Authentication required. Please log in as an admin user.');
      } else {
        setError(errorMessage);
      }
    } finally {
      setLoading(false);
    }
  };

  const fetchAccuracyStats = async () => {
    try {
      const response = await api.get('/api/v1/admin/ai/predictions/accuracy');
      setAccuracyStats(response.data);
    } catch (err) {
      console.error('Failed to load accuracy stats:', err);
    }
  };

  const getPriceChangeClass = (current: number, predicted: number) => {
    const change = ((predicted - current) / current) * 100;
    if (change > 5) return 'price-up-strong';
    if (change > 0) return 'price-up';
    if (change < -5) return 'price-down-strong';
    if (change < 0) return 'price-down';
    return 'price-stable';
  };

  const getConfidenceClass = (confidence: number) => {
    if (confidence >= 80) return 'confidence-high';
    if (confidence >= 60) return 'confidence-medium';
    return 'confidence-low';
  };

  if (loading) return <div className="loading">Loading predictions...</div>;
  if (error) return <div className="error">Error: {error}</div>;

  return (
    <div className="market-prediction-interface">
      <div className="prediction-controls">
        <div className="control-group">
          <label>Timeframe:</label>
          <select value={selectedTimeframe} onChange={(e) => setSelectedTimeframe(e.target.value)}>
            <option value="15m">15 minutes</option>
            <option value="1h">1 hour</option>
            <option value="4h">4 hours</option>
            <option value="1d">1 day</option>
            <option value="1w">1 week</option>
          </select>
        </div>
        <div className="control-group">
          <label>Resource:</label>
          <select value={selectedResource} onChange={(e) => setSelectedResource(e.target.value)}>
            <option value="all">All Resources</option>
            <option value="energy">Energy Cells</option>
            <option value="metal">Refined Metal</option>
            <option value="crystal">Crystal Lattices</option>
            <option value="gas">Quantum Gas</option>
            <option value="water">Pure Water</option>
          </select>
        </div>
      </div>

      <div className="prediction-grid">
        <div className="accuracy-stats">
          <h3>Model Accuracy</h3>
          <div className="accuracy-cards">
            {accuracyStats.map(stat => (
              <div key={stat.resourceId} className="accuracy-card">
                <h4>{stat.resourceName}</h4>
                <div className="accuracy-metric">
                  <span className="metric-value">{(stat.accuracy || 0).toFixed(1)}%</span>
                  <span className="metric-label">Accuracy</span>
                </div>
                <div className="accuracy-details">
                  <span>{stat.accurate || 0}/{stat.predictions || 0} correct</span>
                  <span>±{(stat.avgDeviation || 0).toFixed(2)}% avg deviation</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="predictions-list">
          <h3>Active Predictions</h3>
          <div className="predictions-table">
            <table>
              <thead>
                <tr>
                  <th>Resource</th>
                  <th>Sector</th>
                  <th>Current Price</th>
                  <th>Predicted Price</th>
                  <th>Change</th>
                  <th>Confidence</th>
                  <th>Timeframe</th>
                  <th>Key Factors</th>
                </tr>
              </thead>
              <tbody>
                {predictions.map(prediction => {
                  const priceChange = ((prediction.predictedPrice - prediction.currentPrice) / prediction.currentPrice) * 100;
                  return (
                    <tr key={prediction.id}>
                      <td>{prediction.resourceName}</td>
                      <td>{prediction.sectorName}</td>
                      <td>{prediction.currentPrice.toFixed(2)} ₵</td>
                      <td className={getPriceChangeClass(prediction.currentPrice, prediction.predictedPrice)}>
                        {prediction.predictedPrice.toFixed(2)} ₵
                      </td>
                      <td className={getPriceChangeClass(prediction.currentPrice, prediction.predictedPrice)}>
                        {priceChange > 0 ? '+' : ''}{priceChange.toFixed(1)}%
                      </td>
                      <td>
                        <span className={`confidence-badge ${getConfidenceClass(prediction.confidence)}`}>
                          {prediction.confidence}%
                        </span>
                      </td>
                      <td>{prediction.timeframe}</td>
                      <td>
                        <ul className="factors-list">
                          {prediction.factors.slice(0, 2).map((factor, i) => (
                            <li key={i}>{factor}</li>
                          ))}
                          {prediction.factors.length > 2 && (
                            <li className="more-factors">+{prediction.factors.length - 2} more</li>
                          )}
                        </ul>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
};